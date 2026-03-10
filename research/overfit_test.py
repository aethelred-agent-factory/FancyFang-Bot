#!/usr/bin/env python3
"""
overfit_test.py — Overfitting Diagnostics for FancyFangBot Backtester v1.1
=========================================================================
Runs four tests to determine if your strategy is overfit:

  TEST 1 — REGIME SLICE ANALYSIS
    Splits candles into QUARTERS and shows per-slice performance.
    Tells you exactly WHICH market period drove the returns, not just IS vs OOS.

  TEST 2 — PERMUTATION TEST (100 shuffles)
    Shuffles candle order to destroy temporal structure.
    Crucially reports the RANDOM ENTRY BASELINE so you know how much of your
    PnL comes from signals vs. just riding market drift.

  TEST 3 — RANDOM ENTRY BASELINE
    Enters trades randomly (ignoring all signals), uses your exact exit logic.
    If random entries are consistently profitable, your exit logic / market
    drift is doing the work, not your entry signals.

  TEST 4 — PARAMETER SENSITIVITY
    Perturbs trail_pct by ±25%/50% and min_score by ABSOLUTE ±10/20 points
    (not percentages — at low base scores, % perturbation is meaningless).
    Also tests leverage ±2.

Usage (same params as backtest.py):
  python overfit_test.py --timeframe 1h --candles 1000 --min-score 5 \\
         --trail-pct 0.04 --leverage 5 --stop-loss-pct 0.05 \\
         --direction BOTH --cooldown 5

Place this file in the SAME directory as backtest.py.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import argparse
import concurrent.futures
import math
import random
import sys
import threading
from typing import List

import numpy as np

try:
    import research.backtest as bt
except ImportError:
    try:
        import backtest as bt
    except ImportError:
        sys.exit("❌  Cannot import backtest.py — place this file in the same directory.")

RESET  = "\033[0m"; BOLD = "\033[1m"; CYAN = "\033[96m"
GREEN  = "\033[92m"; RED  = "\033[91m"; YELLOW = "\033[93m"
WHITE  = "\033[97m"; DIM  = "\033[2m"

BAR_WIDTH = 30


def _run(sym_data, kwargs) -> List[bt.Trade]:
    all_trades = []
    for sym, candles, spread, funding, rsi_1h in sym_data:
        all_trades.extend(bt.backtest_symbol(
            sym, candles, spread, funding, rsi_1h, **kwargs
        ))
    return all_trades


def _metrics(trades: List[bt.Trade]) -> dict:
    closed = [t for t in trades if t.pnl_usdt is not None]
    if not closed:
        return dict(n=0, pnl=0.0, wr=0.0, exp=0.0, sharpe=float("nan"), pf=0.0)
    pnls   = [t.pnl_usdt for t in closed]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total  = sum(pnls)
    wr     = len(wins) / len(closed) * 100
    exp    = total / len(closed)
    gw     = sum(wins)
    gl     = abs(sum(losses))
    pf     = gw / gl if gl > 0 else float("inf")
    arr    = np.array(pnls, dtype=float)
    std    = float(np.std(arr, ddof=1))
    sharpe = float(np.mean(arr) / std) if std > 0 else float("nan")
    return dict(n=len(closed), pnl=total, wr=wr, exp=exp, sharpe=sharpe, pf=pf)


def _shuffle_candles(candles):
    c = list(candles)
    random.shuffle(c)
    return c


def _bar(value, max_val, width=BAR_WIDTH, fill="▓", empty="░"):
    if max_val == 0:
        return empty * width
    filled = max(0, min(width, int(round(abs(value) / max_val * width))))
    return fill * filled + empty * (width - filled)


def print_header(title):
    line = "═" * 70
    print(f"\n{CYAN}{BOLD}{line}\n  {title}\n{line}{RESET}")


# ─────────────────────────────────────────────────────────────────────
# TEST 1 — REGIME SLICES (quarters)
# ─────────────────────────────────────────────────────────────────────

def run_regime_slices(sym_data_full, kwargs) -> dict:
    print_header("TEST 1 — REGIME SLICE ANALYSIS  (candles split into quarters)")

    slice_results = []
    for i in range(4):
        sliced = []
        for sym, candles, spread, funding, rsi_1h in sym_data_full:
            total = len(candles)
            lo = int(total * i / 4)
            hi = int(total * (i + 1) / 4)
            chunk = candles[lo:hi]
            if len(chunk) >= 110:
                sliced.append((sym, chunk, spread, funding, rsi_1h))
        trades = _run(sliced, kwargs)
        slice_results.append(_metrics(trades))

    max_abs_pnl = max(abs(m['pnl']) for m in slice_results) or 1.0
    labels = ["Q1 (oldest)", "Q2", "Q3", "Q4 (newest)"]

    print(f"\n  {'Slice':<14} {'Trades':>7} {'WR%':>7} {'PnL':>12} {'Exp':>9}   Chart")
    print(f"  {'─'*75}")
    for m, label in zip(slice_results, labels):
        pnl_col = GREEN if m['pnl'] > 0 else RED
        bar_col = GREEN if m['pnl'] > 0 else RED
        bar = _bar(m['pnl'], max_abs_pnl)
        print(f"  {label:<14} {m['n']:>7} {m['wr']:>6.1f}% "
              f"{pnl_col}{m['pnl']:>+12.4f}{RESET} "
              f"{m['exp']:>+9.4f}   {bar_col}{bar}{RESET}")

    profitable = sum(1 for m in slice_results if m['pnl'] > 0)
    pnls       = [m['pnl'] for m in slice_results]
    mean_pnl   = float(np.mean(pnls))
    std_pnl    = float(np.std(pnls))
    cv         = std_pnl / abs(mean_pnl) if mean_pnl != 0 else float("inf")

    print()
    if profitable == 4:
        verdict = f"{GREEN}✅  CONSISTENT — Profitable in all 4 quarters.{RESET}"
    elif profitable == 3:
        verdict = f"{YELLOW}⚠️   MOSTLY CONSISTENT — 3/4 quarters profitable.{RESET}"
    elif profitable == 2:
        verdict = f"{YELLOW}⚠️   INCONSISTENT — Only 2/4 quarters profitable.{RESET}"
    else:
        verdict = f"{RED}❌  REGIME LOCKED — Profits concentrated in ≤1 quarter.{RESET}"

    print(f"  {verdict}")
    print(f"  {DIM}CV={cv:.2f}  (< 0.5 = consistent across regimes,  > 1.5 = highly scattered){RESET}")
    return dict(slices=slice_results, profitable=profitable, cv=cv)


# ─────────────────────────────────────────────────────────────────────
# TEST 2 — PERMUTATION TEST (drift-adjusted)
# ─────────────────────────────────────────────────────────────────────

def run_permutation(sym_data_full, kwargs, n_permutations=100) -> dict:
    print_header(f"TEST 2 — PERMUTATION TEST  (n={n_permutations} shuffles, drift-adjusted)")

    real_trades = _run(sym_data_full, kwargs)
    real_pnl    = _metrics(real_trades)['pnl']

    print(f"\n  Real strategy PnL : {GREEN if real_pnl > 0 else RED}{real_pnl:+.4f} USDT{RESET}")
    print(f"  Running {n_permutations} permutations", end="", flush=True)

    null_pnls = []
    for i in range(n_permutations):
        shuffled = [(s, _shuffle_candles(c), sp, f, r)
                    for s, c, sp, f, r in sym_data_full]
        null_pnls.append(_metrics(_run(shuffled, kwargs))['pnl'])
        if (i + 1) % 10 == 0:
            print(".", end="", flush=True)
    print()

    null_arr  = np.array(null_pnls)
    null_mean = float(np.mean(null_arr))
    null_std  = float(np.std(null_arr, ddof=1))
    null_p95  = float(np.percentile(null_arr, 95))
    null_p99  = float(np.percentile(null_arr, 99))
    beats     = int(np.sum(null_arr >= real_pnl))
    p_value   = beats / n_permutations
    z_score   = (real_pnl - null_mean) / null_std if null_std > 0 else float("nan")

    drift_edge    = real_pnl - null_mean
    drift_pct     = null_mean / real_pnl * 100 if real_pnl != 0 else 0.0

    print(f"\n  {'Null mean (random baseline)':<32}: {YELLOW}{null_mean:>+10.4f} USDT{RESET}"
          f"  {DIM}← market drift captured by exit logic{RESET}")
    print(f"  {'Null std dev':<32}: {null_std:>+10.4f} USDT")
    print(f"  {'Null 95th pct':<32}: {null_p95:>+10.4f} USDT")
    print(f"  {'Null 99th pct':<32}: {null_p99:>+10.4f} USDT")
    print(f"\n  {'Real PnL':<32}: {GREEN}{real_pnl:>+10.4f} USDT{RESET}")
    da_col = GREEN if drift_edge > 0 else RED
    print(f"  {'Signal-only edge (adj.)':<32}: {da_col}{drift_edge:>+10.4f} USDT{RESET}"
          f"  {DIM}(real minus random baseline){RESET}")

    if real_pnl > 0:
        dc = RED if drift_pct > 40 else (YELLOW if drift_pct > 20 else GREEN)
        print(f"  {'Drift % of total PnL':<32}: {dc}{drift_pct:.1f}%{RESET}"
              f"  {DIM}(lower is better — means signals are doing the work){RESET}")

    print(f"\n  z={z_score:+.2f}σ   p={p_value:.3f}   ({beats}/{n_permutations} shuffles beat real PnL)")

    # Histogram
    all_vals = list(null_pnls) + [real_pnl]
    lo       = min(all_vals) - 1
    hi       = max(all_vals) + 1
    counts, edges = np.histogram(null_pnls, bins=np.linspace(lo, hi, 22))
    max_cnt  = max(counts) if max(counts) > 0 else 1

    print(f"\n  Null PnL distribution  (◀ REAL = your strategy):")
    real_marked = False
    for i, (cnt, edge_lo) in enumerate(zip(counts, edges)):
        marker = ""
        if not real_marked and edges[i] <= real_pnl < edges[i + 1]:
            marker = f"  {CYAN}◀ REAL{RESET}"
            real_marked = True
        bar = _bar(cnt, max_cnt, fill="▓", empty="░")
        print(f"  {edge_lo:>+10.1f}  {bar}  {cnt:>3}{marker}")
    if not real_marked:
        print(f"  {real_pnl:>+10.1f}  {'░'*BAR_WIDTH}    0  {CYAN}◀ REAL{RESET}")

    print()
    if p_value < 0.01:
        base_verdict = f"{GREEN}✅  STRONG (p={p_value:.3f}) — Signals decisively beat shuffled candles.{RESET}"
    elif p_value < 0.05:
        base_verdict = f"{GREEN}✅  SIGNIFICANT (p={p_value:.3f}) — Edge likely real at 95% confidence.{RESET}"
    elif p_value < 0.10:
        base_verdict = f"{YELLOW}⚠️   WEAK (p={p_value:.3f}) — Marginal. Could be noise.{RESET}"
    else:
        base_verdict = f"{RED}❌  NOT SIGNIFICANT (p={p_value:.3f}) — Cannot beat random shuffles.{RESET}"

    print(f"  {base_verdict}")
    if null_mean > 0 and drift_pct > 40:
        print(f"  {YELLOW}⚠️   DRIFT WARNING — {drift_pct:.0f}% of PnL comes from market drift. "
              f"Retest on a sideways or bear period.{RESET}")

    return dict(real_pnl=real_pnl, null_mean=null_mean, null_std=null_std,
                p_value=p_value, z_score=z_score,
                drift_edge=drift_edge, drift_pct=drift_pct)


# ─────────────────────────────────────────────────────────────────────
# TEST 3 — RANDOM ENTRY BASELINE
# ─────────────────────────────────────────────────────────────────────

def run_random_entry(sym_data_full, kwargs, n_runs=50) -> dict:
    """
    Replaces scoring functions with random high scores so every candle triggers
    an entry regardless of signals. Same exit logic applies. Measures how much
    of your PnL comes purely from the exit mechanic + market direction.

    Bug fix: must return enough fake signals to pass the min_signals check,
    and score must exceed any realistic min_score threshold.
    """
    print_header(f"TEST 3 — RANDOM ENTRY BASELINE  (n={n_runs} runs, signals bypassed)")

    orig_long  = bt.score_long_window
    orig_short = bt.score_short_window

    # Need enough dummy signals to pass the min_signals filter (default 3)
    min_sigs = kwargs.get("min_signals", 3)
    dummy_signals = [f"random_signal_{i}" for i in range(max(min_sigs, 5))]

    random_results = []
    print(f"\n  Running {n_runs} random-entry trials", end="", flush=True)

    for run_i in range(n_runs):
        # Capture for closure
        _dummy = list(dummy_signals)

        def random_long(*a, _d=_dummy, **kw):
            # Alternate LONG/SHORT randomly so direction doesn't bias results
            score = random.randint(500, 600)
            return score, list(_d)

        def random_short(*a, _d=_dummy, **kw):
            score = random.randint(500, 600)
            return score, list(_d)

        bt.score_long_window  = random_long
        bt.score_short_window = random_short

        # Override min_score to 1 so our random scores always pass
        kw_rand = dict(kwargs)
        kw_rand["min_score"] = 1

        try:
            trades = _run(sym_data_full, kw_rand)
            random_results.append(_metrics(trades)['pnl'])
        finally:
            bt.score_long_window  = orig_long
            bt.score_short_window = orig_short

        if (run_i + 1) % 10 == 0:
            print(".", end="", flush=True)

    bt.score_long_window  = orig_long
    bt.score_short_window = orig_short

    print()

    arr       = np.array(random_results)
    mean_rand = float(np.mean(arr))
    std_rand  = float(np.std(arr, ddof=1))
    p95_rand  = float(np.percentile(arr, 95))
    pos_runs  = int(np.sum(arr > 0))

    real_pnl    = _metrics(_run(sym_data_full, kwargs))['pnl']
    signal_edge = real_pnl - mean_rand
    re_pct      = mean_rand / real_pnl * 100 if real_pnl != 0 else float("inf")

    mean_col = GREEN if re_pct < 20 else (YELLOW if re_pct < 50 else RED)

    print(f"\n  {'Random entry mean PnL':<30}: {mean_col}{mean_rand:>+10.4f} USDT{RESET}")
    print(f"  {'Random entry std dev':<30}: {std_rand:>+10.4f} USDT")
    print(f"  {'Random entry 95th pct':<30}: {p95_rand:>+10.4f} USDT")
    print(f"  {'Profitable random runs':<30}: {pos_runs}/{n_runs}  ({pos_runs/n_runs*100:.0f}%)")
    print(f"\n  {'Real strategy PnL':<30}: {GREEN}{real_pnl:>+10.4f} USDT{RESET}")
    se_col = GREEN if signal_edge > 0 else RED
    print(f"  {'Signal-only contribution':<30}: {se_col}{signal_edge:>+10.4f} USDT{RESET}"
          f"  {DIM}(real minus random mean){RESET}")
    print(f"  {'Random entry % of real PnL':<30}: {mean_col}{re_pct:.1f}%{RESET}"
          f"  {DIM}(lower = signals doing more work){RESET}")

    print()
    if mean_rand < 0:
        verdict = f"{GREEN}✅  Random entries LOSE money — market direction alone is not enough.{RESET}"
    elif re_pct < 20:
        verdict = f"{GREEN}✅  Random entries = {re_pct:.0f}% of real PnL. Signals are doing most of the work.{RESET}"
    elif re_pct < 50:
        verdict = f"{YELLOW}⚠️   Random entries = {re_pct:.0f}% of real PnL. Significant drift contribution.{RESET}"
    else:
        verdict = f"{RED}❌  Random entries = {re_pct:.0f}% of real PnL. Exit logic / bull market doing most of the work.{RESET}"

    print(f"  {verdict}")

    return dict(mean_rand=mean_rand, std_rand=std_rand,
                real_pnl=real_pnl, signal_edge=signal_edge,
                pos_runs=pos_runs, n_runs=n_runs, re_pct=re_pct)


# ─────────────────────────────────────────────────────────────────────
# TEST 4 — PARAMETER SENSITIVITY (absolute score deltas)
# ─────────────────────────────────────────────────────────────────────

def run_sensitivity(sym_data_full, kwargs) -> dict:
    print_header("TEST 4 — PARAMETER SENSITIVITY  (absolute score deltas, not %)")

    base_m   = _metrics(_run(sym_data_full, kwargs))
    base_pnl = base_m['pnl']
    base_wr  = base_m['wr']

    print(f"\n  Baseline: PnL={GREEN}{base_pnl:+.4f}{RESET}  "
          f"WR={base_wr:.1f}%  n={base_m['n']}  Sharpe={base_m['sharpe']:.3f}\n")

    base_trail = kwargs['trail_pct']
    base_score = kwargs['min_score']
    base_lev   = kwargs['leverage']

    # Use absolute deltas for score: meaningful regardless of base value
    score_step = 10

    perturbations = [
        ("trail_pct", base_trail * 0.50, f"trail_pct × 0.50  ({base_trail*0.50:.3f})"),
        ("trail_pct", base_trail * 0.75, f"trail_pct × 0.75  ({base_trail*0.75:.3f})"),
        ("trail_pct", base_trail * 1.25, f"trail_pct × 1.25  ({base_trail*1.25:.3f})"),
        ("trail_pct", base_trail * 1.50, f"trail_pct × 1.50  ({base_trail*1.50:.3f})"),
        ("min_score", max(1, base_score - score_step * 2), f"min_score - {score_step*2:>2}  → {max(1, base_score - score_step*2)}"),
        ("min_score", max(1, base_score - score_step),     f"min_score - {score_step:>2}  → {max(1, base_score - score_step)}"),
        ("min_score", base_score + score_step,             f"min_score + {score_step:>2}  → {base_score + score_step}"),
        ("min_score", base_score + score_step * 2,         f"min_score + {score_step*2:>2}  → {base_score + score_step*2}"),
        ("leverage",  max(1, base_lev - 2),                f"leverage  - 2    → {max(1, base_lev-2)}x"),
        ("leverage",  base_lev + 2,                        f"leverage  + 2    → {base_lev+2}x"),
    ]

    print(f"  {'Perturbation':<34} {'PnL':>12}   {'WR%':>7}   {'Δ PnL':>11}   Stability")
    print(f"  {'─'*78}")

    results = []
    prev_param = None
    for param, new_val, label in perturbations:
        if prev_param and prev_param != param:
            print(f"  {'─'*78}")
        prev_param = param

        kw = dict(kwargs)
        if param == "min_score":
            kw[param] = int(round(new_val))
        else:
            kw[param] = new_val

        trades = _run(sym_data_full, kw)
        m = _metrics(trades)

        stability = m['pnl'] / base_pnl if base_pnl != 0 else float("nan")
        delta     = m['pnl'] - base_pnl
        identical = abs(delta) < 0.01

        pnl_col  = GREEN if m['pnl'] > 0 else RED
        stab_col = GREEN if stability >= 0.7 else (YELLOW if stability >= 0.4 else RED)
        d_col    = GREEN if delta >= 0 else RED
        id_note  = f"  {YELLOW}[IDENTICAL — parameter not binding]{RESET}" if identical else ""

        print(f"  {label:<34} {pnl_col}{m['pnl']:>+12.4f}{RESET}   "
              f"{m['wr']:>6.1f}%   {d_col}{delta:>+11.4f}{RESET}   "
              f"{stab_col}{stability:>6.2f}x{RESET}{id_note}")

        results.append(dict(param=param, label=label, pnl=m['pnl'],
                            stability=stability, identical=identical))

    stabilities = [r['stability'] for r in results
                   if not math.isnan(r['stability']) and not r['identical']]
    avg_stab    = float(np.mean(stabilities)) if stabilities else 1.0
    n_identical = sum(1 for r in results if r['identical'])

    print()
    if n_identical >= 4:
        print(f"  {YELLOW}⚠️   {n_identical} perturbations returned identical PnL. "
              f"Those thresholds are not binding — likely overridden by ATR scoring "
              f"or the baseline threshold is too permissive.{RESET}\n")

    if avg_stab >= 0.75:
        verdict = f"{GREEN}✅  ROBUST — Stable across parameter variations (avg {avg_stab:.0%}).{RESET}"
    elif avg_stab >= 0.50:
        verdict = f"{YELLOW}⚠️   MODERATE — Some sensitivity (avg {avg_stab:.0%}).{RESET}"
    else:
        verdict = f"{RED}❌  FRAGILE — Collapses with small shifts (avg {avg_stab:.0%}).{RESET}"

    print(f"  {verdict}")

    return dict(base_pnl=base_pnl, results=results,
                avg_stability=avg_stab, n_identical=n_identical)


# ─────────────────────────────────────────────────────────────────────
# Final verdict
# ─────────────────────────────────────────────────────────────────────

def print_final_verdict(regime, perm, rand_ent, sens) -> None:
    print_header("FINAL VERDICT")

    checks = []

    p  = regime['profitable']
    cv = regime['cv']
    if p == 4 and cv < 0.8:
        checks.append((True,  f"Regime consistency:  {p}/4 quarters profitable, CV={cv:.2f}  ✅"))
    elif p >= 3:
        checks.append((None,  f"Regime consistency:  {p}/4 quarters profitable, CV={cv:.2f}  ⚠️"))
    else:
        checks.append((False, f"Regime consistency:  only {p}/4 quarters profitable  ❌"))

    pv    = perm['p_value']
    drift = perm['drift_pct']
    if pv < 0.05 and drift < 30:
        checks.append((True,  f"Signal quality:      p={pv:.3f}, drift={drift:.0f}% of PnL  ✅"))
    elif pv < 0.05:
        checks.append((None,  f"Signal quality:      p={pv:.3f} but drift={drift:.0f}% of PnL  ⚠️"))
    else:
        checks.append((False, f"Signal quality:      p={pv:.3f} (not significant)  ❌"))

    re_pct = rand_ent['re_pct']
    if re_pct < 20:
        checks.append((True,  f"Entry signal value:  random entries = {re_pct:.0f}% of real PnL  ✅"))
    elif re_pct < 50:
        checks.append((None,  f"Entry signal value:  random entries = {re_pct:.0f}% of real PnL  ⚠️"))
    else:
        checks.append((False, f"Entry signal value:  random entries = {re_pct:.0f}% of real PnL  ❌"))

    avg = sens['avg_stability']
    ni  = sens['n_identical']
    if avg >= 0.75 and ni < 4:
        checks.append((True,  f"Parameter stability: avg={avg:.0%}, {ni} identical  ✅"))
    elif avg >= 0.50 or ni >= 4:
        checks.append((None,  f"Parameter stability: avg={avg:.0%}, {ni} identical  ⚠️"))
    else:
        checks.append((False, f"Parameter stability: avg={avg:.0%}  ❌"))

    passes   = sum(1 for ok, _ in checks if ok is True)
    warnings = sum(1 for ok, _ in checks if ok is None)
    fails    = sum(1 for ok, _ in checks if ok is False)

    print()
    for ok, msg in checks:
        col = GREEN if ok else (YELLOW if ok is None else RED)
        print(f"  {col}{msg}{RESET}")

    print()
    if fails == 0 and warnings <= 1:
        print(f"  {GREEN}{BOLD}🏆  CLEAN — No significant overfitting. Edge appears genuine.{RESET}")
    elif fails == 0:
        print(f"  {YELLOW}{BOLD}⚠️   CAUTIOUS PASS — Minor concerns. Run on fresh data before going live.{RESET}")
    elif fails == 1 and passes >= 2:
        print(f"  {YELLOW}{BOLD}⚠️   MIXED — One hard failure. Strategy may be regime-dependent. "
              f"Paper trade first.{RESET}")
    else:
        print(f"  {RED}{BOLD}🚨  OVERFIT WARNING — {fails} tests failed. "
              f"Do NOT trust backtest PnL at face value.{RESET}")

    print()
    print(f"  {DIM}Interpretation guide:{RESET}")
    print(f"  {DIM}  Regime fail + permutation pass  → Real signals, wrong market period (regime overfit){RESET}")
    print(f"  {DIM}  Permutation fail                → No real edge (signals are noise){RESET}")
    print(f"  {DIM}  Random entry > 50% of real PnL  → Exit logic / bull drift doing the work{RESET}")
    print(f"  {DIM}  Sensitivity 'IDENTICAL' rows     → That parameter is not a binding constraint{RESET}")
    print()


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="FancyFangBot Overfit Diagnostics v1.1",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbols",         nargs="+", default=[])
    parser.add_argument("--timeframe",       default="15m")
    parser.add_argument("--candles",         type=int,   default=1000)
    parser.add_argument("--min-score",       type=int,   default=5,      dest="min_score")
    parser.add_argument("--min-signals",     type=int,   default=3,      dest="min_signals")
    parser.add_argument("--trail-pct",       type=float, default=0.02,   dest="trail_pct")
    parser.add_argument("--leverage",        type=int,   default=5)
    parser.add_argument("--margin",          type=float, default=5.0)
    parser.add_argument("--max-margin",      type=float, default=150.0,  dest="max_margin")
    parser.add_argument("--max-hold",        type=int,   default=96,     dest="max_hold")
    parser.add_argument("--min-vol",         type=float, default=5_000_000, dest="min_vol")
    parser.add_argument("--stop-loss-pct",   type=float, default=0.0,    dest="stop_loss_pct")
    parser.add_argument("--take-profit-pct", type=float, default=0.0,    dest="take_profit_pct")
    parser.add_argument("--cooldown",        type=int,   default=0)
    parser.add_argument("--direction",       default="BOTH", choices=["LONG","SHORT","BOTH"])
    parser.add_argument("--min-score-gap",   type=int,   default=0,      dest="min_score_gap")
    parser.add_argument("--workers",         type=int,   default=30)
    parser.add_argument("--no-htf",         action="store_true",         dest="no_htf")
    parser.add_argument("--permutations",   type=int,   default=100,
                        help="Permutation count for Test 2 (use 500 for tighter p-values)")
    parser.add_argument("--random-runs",    type=int,   default=50,      dest="random_runs",
                        help="Random entry runs for Test 3")
    args = parser.parse_args()

    print(f"\n{CYAN}{BOLD}  ╔═══════════════════════════════════════════════════╗")
    print(f"  ║  FancyFangBot OVERFIT DIAGNOSTIC v1.1               ║")
    print(f"  ║  Regime · Permutation · Random Entry · Sensitivity║")
    print(f"  ╚═══════════════════════════════════════════════════╝{RESET}\n")

    if args.symbols:
        symbols = args.symbols
    else:
        print(f"{WHITE}  Fetching ticker universe...{RESET}", end="", flush=True)
        tickers = bt.get_tickers(min_vol=args.min_vol)
        tickers.sort(key=lambda t: float(t.get("turnoverRv") or 0), reverse=True)
        symbols = [t["symbol"] for t in tickers[:50]]
        print(f" {len(symbols)} symbols")

    print(f"{WHITE}  Fetching {args.candles}x {args.timeframe} candles"
          f"{' + 1H RSI' if not args.no_htf else ''}...{RESET}")

    sym_data = []
    lock = threading.Lock()
    done = [0]

    def fetch(sym):
        candles = bt.get_candles(sym, timeframe=args.timeframe, limit=args.candles)
        spread  = bt.get_spread_pct(sym)
        funding = bt.get_funding(sym)
        rsi_1h  = None if args.no_htf else bt.get_htf_rsi(sym)
        with lock:
            sym_data.append((sym, candles, spread, funding, rsi_1h))
            done[0] += 1
            print(f"\r  Fetching: {done[0]}/{len(symbols)}", end="", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        ex.map(fetch, symbols)
    print()

    valid = [(s, c, sp, f, r) for s, c, sp, f, r in sym_data if len(c) >= 220]
    print(f"  {len(valid)}/{len(symbols)} symbols with sufficient data (≥220 candles)\n")
    if not valid:
        sys.exit("❌  No valid data.")

    bt_kwargs = dict(
        min_score       = args.min_score,
        trail_pct       = args.trail_pct,
        leverage        = args.leverage,
        margin          = args.margin,
        max_margin      = args.max_margin,
        max_hold        = args.max_hold,
        hard_stop_pct   = args.stop_loss_pct,
        take_profit_pct = args.take_profit_pct,
        cooldown        = args.cooldown,
        direction       = args.direction,
        min_score_gap   = args.min_score_gap,
        min_signals     = args.min_signals,
    )

    regime   = run_regime_slices(valid, bt_kwargs)
    perm     = run_permutation(valid, bt_kwargs, n_permutations=args.permutations)
    rand_ent = run_random_entry(valid, bt_kwargs, n_runs=args.random_runs)
    sens     = run_sensitivity(valid, bt_kwargs)

    print_final_verdict(regime, perm, rand_ent, sens)


if __name__ == "__main__":
    main()
