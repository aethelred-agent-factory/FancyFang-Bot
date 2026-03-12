#!/usr/bin/env python3
"""
overfit.py — Overfitting Diagnostics wrapper for FancyFangBot Backtester
=====================================================================
This file is a compatibility entrypoint that exposes the same functionality
as `overfit_test.py` but is named `overfit.py` so existing commands work.

It mostly mirrors `overfit_test.py` and accepts an extra `--window` argument
for compatibility with older CLI usages (the value is currently unused).
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
import json

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
    # No-op for web output, headers will be part of the returned dict
    pass


# The implementation below is intentionally identical to overfit_test.py
# to keep behavior consistent; tests and JSON output are unchanged.


def run_regime_slices(sym_data_full, kwargs) -> dict:
    slice_results_raw = []
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
        slice_results_raw.append(_metrics(trades))

    profitable = sum(1 for m in slice_results_raw if m['pnl'] > 0)
    pnls       = [m['pnl'] for m in slice_results_raw]
    mean_pnl   = float(np.mean(pnls))
    std_pnl    = float(np.std(pnls))
    cv         = std_pnl / abs(mean_pnl) if mean_pnl != 0 else float("inf")

    if profitable == 4:
        verdict = "CONSISTENT — Profitable in all 4 quarters."
    elif profitable == 3:
        verdict = "MOSTLY CONSISTENT — 3/4 quarters profitable."
    elif profitable == 2:
        verdict = "INCONSISTENT — Only 2/4 quarters profitable."
    else:
        verdict = "REGIME LOCKED — Profits concentrated in ≤1 quarter."

    return {
        "title": "TEST 1 — REGIME SLICE ANALYSIS",
        "verdict": verdict,
        "cv": round(cv, 2),
        "slices": [{
            "label": f"Q{i+1}",
            "trades": m['n'],
            "win_rate": round(m['wr'], 1),
            "pnl": round(m['pnl'], 4),
            "expectancy": round(m['exp'], 4),
        } for i, m in enumerate(slice_results_raw)],
    }


def run_permutation(sym_data_full, kwargs, n_permutations=100) -> dict:
    real_trades = _run(sym_data_full, kwargs)
    real_pnl    = _metrics(real_trades)['pnl']

    null_pnls = []
    for i in range(n_permutations):
        shuffled = [(s, _shuffle_candles(c), sp, f, r)
                    for s, c, sp, f, r in sym_data_full]
        null_pnls.append(_metrics(_run(shuffled, kwargs))['pnl'])

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

    if p_value < 0.01:
        base_verdict = f"STRONG (p={p_value:.3f}) — Signals decisively beat shuffled candles."
    elif p_value < 0.05:
        base_verdict = f"SIGNIFICANT (p={p_value:.3f}) — Edge likely real at 95% confidence."
    elif p_value < 0.10:
        base_verdict = f"WEAK (p={p_value:.3f}) — Marginal. Could be noise."
    else:
        base_verdict = f"NOT SIGNIFICANT (p={p_value:.3f}) — Cannot beat random shuffles."

    drift_warning = None
    if null_mean > 0 and drift_pct > 40:
        drift_warning = f"DRIFT WARNING — {drift_pct:.0f}% of PnL comes from market drift. Retest on a sideways or bear period."

    return {
        "title": "TEST 2 — PERMUTATION TEST",
        "real_pnl": round(real_pnl, 4),
        "null_mean": round(null_mean, 4),
        "null_std": round(null_std, 4),
        "p_value": round(p_value, 3),
        "z_score": round(z_score, 2),
        "drift_edge": round(drift_edge, 4),
        "drift_pct": round(drift_pct, 1),
        "verdict": base_verdict,
        "drift_warning": drift_warning,
    }


def run_random_entry(sym_data_full, kwargs, n_runs=50) -> dict:
    orig_long  = bt.score_long_window
    orig_short = bt.score_short_window

    min_sigs = kwargs.get("min_signals", 3)
    dummy_signals = [f"random_signal_{i}" for i in range(max(min_sigs, 5))]

    random_results = []

    for run_i in range(n_runs):
        _dummy = list(dummy_signals)

        def random_long(*a, _d=_dummy, **kw):
            score = random.randint(500, 600)
            return score, list(_d)

        def random_short(*a, _d=_dummy, **kw):
            score = random.randint(500, 600)
            return score, list(_d)

        bt.score_long_window  = random_long
        bt.score_short_window = random_short

        kw_rand = dict(kwargs)
        kw_rand["min_score"] = 1

        try:
            trades = _run(sym_data_full, kw_rand)
            random_results.append(_metrics(trades)['pnl'])
        finally:
            bt.score_long_window  = orig_long
            bt.score_short_window = orig_short

    bt.score_long_window  = orig_long
    bt.score_short_window = orig_short

    arr       = np.array(random_results)
    mean_rand = float(np.mean(arr))
    std_rand  = float(np.std(arr, ddof=1))
    p95_rand  = float(np.percentile(arr, 95))
    pos_runs  = int(np.sum(arr > 0))

    real_pnl    = _metrics(_run(sym_data_full, kwargs))['pnl']
    signal_edge = real_pnl - mean_rand
    re_pct      = mean_rand / real_pnl * 100 if real_pnl != 0 else float("inf")

    if mean_rand < 0:
        verdict = "Random entries LOSE money — market direction alone is not enough."
    elif re_pct < 20:
        verdict = f"Random entries = {re_pct:.0f}% of real PnL. Signals are doing most of the work."
    elif re_pct < 50:
        verdict = f"Random entries = {re_pct:.0f}% of real PnL. Significant drift contribution."
    else:
        verdict = f"Random entries = {re_pct:.0f}% of real PnL. Exit logic / bull market doing most of the work."

    return {
        "title": "TEST 3 — RANDOM ENTRY BASELINE",
        "mean_random_pnl": round(mean_rand, 4),
        "std_random_pnl": round(std_rand, 4),
        "profitable_random_runs": pos_runs,
        "total_random_runs": n_runs,
        "real_pnl": round(real_pnl, 4),
        "signal_contribution": round(signal_edge, 4),
        "random_pct_of_real": round(re_pct, 1),
        "verdict": verdict,
    }


def run_sensitivity(sym_data_full, kwargs) -> dict:
    base_m   = _metrics(_run(sym_data_full, kwargs))
    base_pnl = base_m['pnl']
    base_wr  = base_m['wr']

    base_trail = kwargs['trail_pct']
    base_score = kwargs['min_score']
    base_lev   = kwargs['leverage']

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

    results_formatted = []
    prev_param = None
    for param, new_val, label in perturbations:
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

        results_formatted.append({
            "param": param,
            "label": label,
            "pnl": round(m['pnl'], 4),
            "stability": round(stability, 2) if not math.isnan(stability) else "nan",
            "delta_pnl": round(delta, 4),
            "identical": identical,
        })

    stabilities = [r['stability'] for r in results_formatted
                   if not math.isnan(r['stability']) and not r['identical']]
    avg_stab    = float(np.mean(stabilities)) if stabilities else 1.0
    n_identical = sum(1 for r in results_formatted if r['identical'])

    if avg_stab >= 0.75:
        verdict = f"ROBUST — Stable across parameter variations (avg {avg_stab:.0%})."
    elif avg_stab >= 0.50:
        verdict = f"MODERATE — Some sensitivity (avg {avg_stab:.0%})."
    else:
        verdict = f"FRAGILE — Collapses with small shifts (avg {avg_stab:.0%})."

    return {
        "title": "TEST 4 — PARAMETER SENSITIVITY",
        "base_pnl": round(base_pnl, 4),
        "verdict": verdict,
        "warning": f"{n_identical} perturbations returned identical PnL. Those thresholds are not binding — likely overridden by ATR scoring or the baseline threshold is too permissive." if n_identical >= 4 else None,
        "avg_stability": round(avg_stab, 2),
        "n_identical": n_identical,
        "perturbations": results_formatted,
    }


def run_final_verdict(regime, perm, rand_ent, sens) -> dict:
    checks = []

    p  = regime.get('profitable', sum(1 for s in regime.get('slices', []) if s.get('pnl',0) > 0))
    cv = regime.get('cv', 0)
    if p == 4 and cv < 0.8:
        checks.append({"status": "PASS", "message": f"Regime consistency: {p}/4 quarters profitable, CV={cv:.2f}"})
    elif p >= 3:
        checks.append({"status": "WARN", "message": f"Regime consistency: {p}/4 quarters profitable, CV={cv:.2f}"})
    else:
        checks.append({"status": "FAIL", "message": f"Regime consistency: only {p}/4 quarters profitable"})

    pv    = perm['p_value']
    drift = perm['drift_pct']
    if pv < 0.05 and drift < 30:
        checks.append({"status": "PASS", "message": f"Signal quality: p={pv:.3f}, drift={drift:.0f}% of PnL"})
    elif pv < 0.05:
        checks.append({"status": "WARN", "message": f"Signal quality: p={pv:.3f} but drift={drift:.0f}% of PnL"})
    else:
        checks.append({"status": "FAIL", "message": f"Signal quality: p={pv:.3f} (not significant)"})

    re_pct = rand_ent['random_pct_of_real']
    if re_pct < 20:
        checks.append({"status": "PASS", "message": f"Entry signal value: random entries = {re_pct:.0f}% of real PnL"})
    elif re_pct < 50:
        checks.append({"status": "WARN", "message": f"Entry signal value: random entries = {re_pct:.0f}% of real PnL. Significant drift contribution."})
    else:
        checks.append({"status": "FAIL", "message": f"Entry signal value: random entries = {re_pct:.0f}% of real PnL. Exit logic / bull market doing most of the work."})

    avg = sens['avg_stability']
    ni  = sens['n_identical']
    if avg >= 0.75 and ni < 4:
        checks.append({"status": "PASS", "message": f"Parameter stability: avg={avg:.0%}, {ni} identical"})
    elif avg >= 0.50 or ni >= 4:
        checks.append({"status": "WARN", "message": f"Parameter stability: avg={avg:.0%}, {ni} identical"})
    else:
        checks.append({"status": "FAIL", "message": f"Parameter stability: avg={avg:.0%}"})

    passes   = sum(1 for c in checks if c["status"] == "PASS")
    warnings = sum(1 for c in checks if c["status"] == "WARN")
    fails    = sum(1 for c in checks if c["status"] == "FAIL")

    if fails == 0 and warnings <= 1:
        overall_verdict = "CLEAN — No significant overfitting. Edge appears genuine."
    elif fails == 0:
        overall_verdict = "CAUTIOUS PASS — Minor concerns. Run on fresh data before going live."
    elif fails == 1 and passes >= 2:
        overall_verdict = "MIXED — One hard failure. Strategy may be regime-dependent. Paper trade first."
    else:
        overall_verdict = f"OVERFIT WARNING — {fails} tests failed. Do NOT trust backtest PnL at face value."

    return {
        "title": "FINAL VERDICT",
        "overall_verdict": overall_verdict,
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(
        description="FancyFangBot Overfit Diagnostics (compat wrapper)",
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
    # Compatibility option used by some callers; currently unused here
    parser.add_argument("--window",         type=int,   default=150,     dest="window",
                        help="Compatibility: window size (unused)")
    args = parser.parse_args()

    if os.environ.get("OVERFIT_TEST_WEB_MODE") == "1":
        f = open(os.devnull, 'w')
        sys.stdout = f
        sys.stderr = f

    if args.symbols:
        symbols = args.symbols
    else:
        tickers = bt.get_tickers(min_vol=args.min_vol)
        tickers.sort(key=lambda t: float(t.get("turnoverRv") or 0), reverse=True)
        symbols = [t["symbol"] for t in tickers[:50]]

    sym_data = []
    lock = threading.Lock()

    def fetch(sym):
        candles = bt.get_candles(sym, timeframe=args.timeframe, limit=args.candles)
        spread  = bt.get_spread_pct(sym)
        funding = bt.get_funding(sym)
        rsi_1h  = None if args.no_htf else bt.get_htf_rsi(sym)
        with lock:
            sym_data.append((sym, candles, spread, funding, rsi_1h))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        ex.map(fetch, symbols)

    valid = [(s, c, sp, f, r) for s, c, sp, f, r in sym_data if len(c) >= 220]
    if not valid:
        return {"error": "No valid data for overfitting test."}

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

    final_verdict = run_final_verdict(regime, perm, rand_ent, sens)

    return {
        "params": {k: getattr(args, k) for k in vars(args) if k not in ["symbols", "permutations", "random_runs", "workers", "no_htf"]},
        "regime_analysis": regime,
        "permutation_test": perm,
        "random_entry_baseline": rand_ent,
        "parameter_sensitivity": sens,
        "final_verdict": final_verdict,
    }


if __name__ == "__main__":
    results = main()
    if results:
        print(json.dumps(results, indent=2))
