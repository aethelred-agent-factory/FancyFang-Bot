#!/usr/bin/env python3
"""
run_all.py — Parallel backtest runner + aggregator
=====================================================
Reads every command from allday.sh, runs them in parallel worker pools,
captures each backtest_results.json, tags every trade with the run's
parameters, and streams everything into one master JSON file.

Usage:
    python run_all.py                          # uses allday.sh, 6 workers
    python run_all.py --sh allday.sh           # explicit path
    python run_all.py --workers 12             # more parallelism
    python run_all.py --workers 4 --start 500  # resume from line 500
    python run_all.py --limit 1000             # only run first 1000 commands
    python run_all.py --dry-run                # print first 5 commands, exit

Outputs:
    master_results.json   — every trade from every run, with params attached
    run_summary.json      — per-run summary (params + stats, no trade detail)
    progress.txt          — last completed line number (for resuming)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
DEFAULT_SH       = "allday.sh"
DEFAULT_WORKERS  = 6
MASTER_FILE      = "master_results.json"
SUMMARY_FILE     = "run_summary.json"
PROGRESS_FILE    = "progress.txt"
SAVE_EVERY       = 50     # flush to disk every N completed runs
TIMEOUT_SECONDS  = 120    # kill a run if it takes longer than this

# ── PARAM PARSER ──────────────────────────────────────────────────────────────
PARAM_RE = re.compile(r'--([\w-]+)\s+(\S+)')
FLAG_RE  = re.compile(r'--(no-htf|csv)\b')

def parse_params(cmd: str) -> dict:
    """Extract all --key value pairs and boolean flags from a command string."""
    params = {}
    for key, val in PARAM_RE.findall(cmd):
        try:    params[key] = float(val) if '.' in val else int(val)
        except: params[key] = val
    for flag in FLAG_RE.findall(cmd):
        params[flag] = True
    params.setdefault('no-htf', False)
    params.setdefault('csv', False)
    return params

# ── SINGLE RUN ────────────────────────────────────────────────────────────────
def run_one(cmd: str, run_id: int) -> dict | None:
    """
    Run one backtest command in an isolated temp directory.
    Returns a result dict or None on failure.
    """
    tmpdir = tempfile.mkdtemp(prefix=f"bt_{run_id}_")
    try:
        # Strip 'python backtest.py' and rebuild relative to cwd
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=os.getcwd(),           # run from your project dir
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )

        json_path = Path(tmpdir) / "backtest_results.json"

        # backtest.py writes to cwd, not tmpdir — look in cwd first
        cwd_json = Path(os.getcwd()) / "backtest_results.json"

        # Since multiple workers share cwd we do a quick snatch-and-parse
        # right after the process exits (slight race, but usually fine with
        # --workers ≤ cpu_count).  Prefer the subprocess stdout if it embeds
        # the JSON there (some versions do).
        trades = None

        # Try stdout first (some backtest.py versions print JSON)
        try:
            trades = json.loads(result.stdout)
            if not isinstance(trades, list):
                trades = None
        except Exception:
            pass

        # Fall back to backtest_results.json in cwd
        if trades is None and cwd_json.exists():
            try:
                with open(cwd_json) as f:
                    trades = json.load(f)
                if not isinstance(trades, list):
                    trades = None
            except Exception:
                pass

        if not trades:
            return {
                "run_id":  run_id,
                "cmd":     cmd,
                "params":  parse_params(cmd),
                "status":  "no_trades",
                "trades":  [],
                "stats":   {},
            }

        params = parse_params(cmd)

        # Tag every trade with run metadata
        for t in trades:
            t["run_id"]    = run_id
            t["params"]    = params

        # Quick stats for summary
        wins  = [t for t in trades if t.get("pnl_usdt", 0) > 0]
        total_pnl = sum(t.get("pnl_usdt", 0) for t in trades)
        stats = {
            "n_trades":    len(trades),
            "n_wins":      len(wins),
            "win_rate":    round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "total_pnl":   round(total_pnl, 4),
            "expectancy":  round(total_pnl / len(trades), 4) if trades else 0,
        }

        return {
            "run_id": run_id,
            "cmd":    cmd,
            "params": params,
            "status": "ok",
            "trades": trades,
            "stats":  stats,
        }

    except subprocess.TimeoutExpired:
        return {"run_id": run_id, "cmd": cmd, "params": parse_params(cmd),
                "status": "timeout", "trades": [], "stats": {}}
    except Exception as e:
        return {"run_id": run_id, "cmd": cmd, "params": parse_params(cmd),
                "status": f"error:{e}", "trades": [], "stats": {}}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ── PROGRESS BAR ─────────────────────────────────────────────────────────────
lock = threading.Lock()
completed = 0
total_runs = 0

def progress(done, total, pnl, errors):
    bar_len = 30
    filled  = int(bar_len * done / total) if total else 0
    bar     = "█" * filled + "░" * (bar_len - filled)
    pct     = done / total * 100 if total else 0
    sys.stdout.write(
        f"\r  [{bar}] {pct:>5.1f}%  {done}/{total}  "
        f"PnL: {pnl:>+10.2f}  errors: {errors}   "
    )
    sys.stdout.flush()

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    global completed, total_runs

    ap = argparse.ArgumentParser()
    ap.add_argument("--sh",      default=DEFAULT_SH)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--start",   type=int, default=0,
                    help="Skip first N commands (resume)")
    ap.add_argument("--limit",   type=int, default=0,
                    help="Only run this many commands (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # ── Load commands ──────────────────────────────────────────────────────
    sh_path = Path(args.sh)
    if not sh_path.exists():
        print(f"  ✗ Cannot find {args.sh}")
        sys.exit(1)

    cmds = [
        line.strip()
        for line in sh_path.read_text().splitlines()
        if line.strip().startswith("python ")
    ]
    print(f"\n  Loaded {len(cmds):,} commands from {args.sh}")

    # Resume support
    start = args.start
    if start == 0 and Path(PROGRESS_FILE).exists():
        start = int(Path(PROGRESS_FILE).read_text().strip())
        print(f"  Resuming from command #{start}")

    cmds = cmds[start:]
    if args.limit:
        cmds = cmds[:args.limit]

    total_runs = len(cmds)
    print(f"  Running {total_runs:,} commands with {args.workers} workers\n")

    if args.dry_run:
        for c in cmds[:5]: print(f"  {c}")
        return

    # ── Setup output ───────────────────────────────────────────────────────
    all_trades   = []
    all_summaries = []
    running_pnl  = 0.0
    errors       = 0
    batch        = []

    def flush(force=False):
        nonlocal all_trades, all_summaries
        if not batch: return
        with open(MASTER_FILE, "a") as f:
            for t in batch:
                f.write(json.dumps(t) + "\n")
        batch.clear()

    # Write header comment to master file (NDJSON format — one trade per line)
    if not Path(MASTER_FILE).exists():
        with open(MASTER_FILE, "w") as f:
            f.write("")  # start fresh

    # ── Run ────────────────────────────────────────────────────────────────
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_one, cmd, start + i): (start + i, cmd)
            for i, cmd in enumerate(cmds)
        }

        for future in as_completed(futures):
            run_id, cmd = futures[future]
            result = future.result()

            with lock:
                completed += 1

                if result["status"] != "ok":
                    errors += 1
                else:
                    pnl = result["stats"].get("total_pnl", 0)
                    running_pnl += pnl
                    all_summaries.append({
                        "run_id": result["run_id"],
                        "params": result["params"],
                        "stats":  result["stats"],
                    })
                    batch.extend(result["trades"])

                # Save progress
                Path(PROGRESS_FILE).write_text(str(run_id + 1))

                # Flush trades to disk periodically
                if len(batch) >= SAVE_EVERY * 10:
                    flush()

                progress(completed, total_runs, running_pnl, errors)

    flush()  # final flush

    # ── Save summary ───────────────────────────────────────────────────────
    with open(SUMMARY_FILE, "w") as f:
        json.dump(all_summaries, f)

    elapsed = time.time() - t0
    print(f"\n\n  ✓ Done in {elapsed/60:.1f} min")
    print(f"  ✓ {len(all_summaries):,} successful runs")
    print(f"  ✓ Trades → {MASTER_FILE}  (NDJSON, one trade per line)")
    print(f"  ✓ Summary → {SUMMARY_FILE}")
    print(f"  ✓ Cumulative PnL across all runs: {running_pnl:+,.2f}")
    print(f"  ✗ {errors} errors/timeouts\n")

    # ── Quick top 10 param combos by expectancy ────────────────────────────
    if all_summaries:
        print("  TOP 10 RUNS BY EXPECTANCY:")
        top = sorted(all_summaries,
                     key=lambda x: x["stats"].get("expectancy", -999),
                     reverse=True)[:10]
        for r in top:
            s = r["stats"]; p = r["params"]
            print(
                f"  exp={s['expectancy']:>+7.3f} | "
                f"WR={s['win_rate']:>5.1f}% | "
                f"n={s['n_trades']:>4} | "
                f"PnL={s['total_pnl']:>+9.2f} | "
                f"tf={p.get('timeframe','?')} trail={p.get('trail-pct','?')} "
                f"score≥{p.get('min-score','?')} hold={p.get('max-hold','?')}"
            )


if __name__ == "__main__":
    main()
