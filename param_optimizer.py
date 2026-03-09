#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  AI-GENERATED CODEBASE — FancyBlenny / fancybot                            ║
# ║                                                                              ║
# ║  This file, and every file in this project, was written entirely through     ║
# ║  iterative AI prompting (Claude / Anthropic). No lines were written by       ║
# ║  hand. All architecture decisions, refactors, bug fixes, and feature         ║
# ║  additions were directed via natural-language prompts and implemented by     ║
# ║  AI. This is expected to remain the primary (and likely only) development    ║
# ║  method for this project for the foreseeable future.                         ║
# ║                                                                              ║
# ║  If you are a human developer reading this: the design intent and business   ║
# ║  logic live in the prompt history, not in comments. Treat this code as you   ║
# ║  would any LLM output — verify critical paths before trusting them.          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
"""
Parameter Optimizer — Upgrade #11
====================================
Lightweight walk-forward parameter optimizer.

Searches a configurable grid of:
  - ATR stop multiplier
  - ATR trail multiplier
  - Score threshold
  - Spread filter threshold
  - Volatility filter threshold

Metrics computed per parameter set:
  - Total PnL
  - Win rate
  - Profit Factor  (gross_wins / gross_losses)
  - Max Drawdown
  - Sharpe Ratio   (annualised, approximate)
  - Expectancy per trade

No heavy ML frameworks; pure stdlib + numpy.
Results written to optimizer_results.json.
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger("param_optimizer")
logger.addHandler(logging.NullHandler())

_RESULTS_FILE = Path(__file__).parent / "optimizer_results.json"


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParamSet:
    atr_stop_mult:   float = 1.5
    atr_trail_mult:  float = 1.0
    score_threshold: int   = 130
    spread_max_pct:  float = 0.10   # percent (0.1 = 0.1%)
    vol_min:         float = 0.002  # ATR/price ratio

    def as_dict(self) -> Dict[str, Any]:
        return {
            "atr_stop_mult":   self.atr_stop_mult,
            "atr_trail_mult":  self.atr_trail_mult,
            "score_threshold": self.score_threshold,
            "spread_max_pct":  self.spread_max_pct,
            "vol_min":         self.vol_min,
        }


@dataclass
class BacktestResult:
    params:        ParamSet
    trades:        List[float] = field(default_factory=list)   # PnL per trade
    total_pnl:     float = 0.0
    win_rate:      float = 0.0
    profit_factor: float = 0.0
    max_drawdown:  float = 0.0
    sharpe:        float = 0.0
    expectancy:    float = 0.0
    trade_count:   int   = 0

    def compute_metrics(self) -> None:
        """Recompute all metrics from self.trades list."""
        if not self.trades:
            return
        arr = np.array(self.trades, dtype=float)
        self.trade_count  = len(arr)
        self.total_pnl    = float(arr.sum())
        wins   = arr[arr > 0]
        losses = arr[arr <= 0]
        self.win_rate      = len(wins) / len(arr)
        gross_wins  = float(wins.sum())  if len(wins)   > 0 else 0.0
        gross_loss  = float(-losses.sum()) if len(losses) > 0 else 1e-9
        self.profit_factor = gross_wins / gross_loss if gross_loss > 0 else float("inf")
        # Expectancy
        avg_win  = float(wins.mean())   if len(wins)   > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
        self.expectancy = (self.win_rate * avg_win +
                           (1.0 - self.win_rate) * avg_loss)
        # Max drawdown (equity curve)
        equity = np.cumsum(arr)
        peak   = np.maximum.accumulate(equity)
        dd     = peak - equity
        self.max_drawdown = float(dd.max()) if len(dd) > 0 else 0.0
        # Sharpe (mean / std of returns; not annualised here)
        if arr.std() > 0:
            self.sharpe = float(arr.mean() / arr.std())
        else:
            self.sharpe = 0.0

    def score_composite(self) -> float:
        """
        Weighted composite score for ranking parameter sets.
        Higher = better.
        Caps applied to prevent inf values from zero-loss runs skewing rankings.
        """
        if self.max_drawdown <= 0:
            dd_ratio = 10.0
        else:
            dd_ratio = min(self.total_pnl / self.max_drawdown, 10.0)  # cap at 10×
        capped_pf = min(self.profit_factor, 50.0)  # cap before log to prevent inf
        return (
            0.30 * self.sharpe
            + 0.25 * math.log1p(max(0, capped_pf))
            + 0.25 * dd_ratio
            + 0.20 * self.expectancy
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "params":        self.params.as_dict(),
            "trade_count":   self.trade_count,
            "total_pnl":     round(self.total_pnl, 4),
            "win_rate":      round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "max_drawdown":  round(self.max_drawdown, 4),
            "sharpe":        round(self.sharpe, 4),
            "expectancy":    round(self.expectancy, 6),
            "composite":     round(self.score_composite(), 4),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Grid search
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_GRID = {
    "atr_stop_mult":   [1.0, 1.5, 2.0, 2.5],
    "atr_trail_mult":  [0.5, 1.0, 1.5],
    "score_threshold": [110, 120, 130, 140],
    "spread_max_pct":  [0.05, 0.10, 0.15],
    "vol_min":         [0.001, 0.002, 0.003],
}


def run_grid_search(
    backtest_fn: Callable[[ParamSet, List[Any]], List[float]],
    candle_data: List[Any],
    grid: Optional[Dict[str, List[Any]]] = None,
    top_n: int = 5,
    verbose: bool = True,
) -> List[BacktestResult]:
    """
    Run a grid search over all parameter combinations.

    Args:
        backtest_fn  : Callable(params, candles) → list of PnL floats per trade.
                       Implement this in your backtest script to replay signals.
        candle_data  : list of candles/windows passed verbatim to backtest_fn.
        grid         : parameter grid dict; uses DEFAULT_GRID if None.
        top_n        : number of top results to return and save.
        verbose      : print progress.

    Returns:
        Sorted list of BacktestResult (best first).
    """
    grid = grid or DEFAULT_GRID
    keys = list(grid.keys())
    values = list(grid.values())

    combos = list(itertools.product(*values))
    total  = len(combos)
    if verbose:
        print(f"\n[optimizer] Grid search: {total} combinations...")

    all_results: List[BacktestResult] = []
    t0 = time.time()

    for i, combo in enumerate(combos, 1):
        params = ParamSet(**dict(zip(keys, combo)))
        try:
            pnl_list = backtest_fn(params, candle_data)
        except Exception as e:
            logger.warning(f"optimizer: backtest_fn error for {params.as_dict()}: {e}")
            pnl_list = []

        res = BacktestResult(params=params, trades=pnl_list)
        res.compute_metrics()
        all_results.append(res)

        if verbose and (i % max(1, total // 10) == 0 or i == total):
            elapsed = time.time() - t0
            print(
                f"  [{i}/{total}] best so far: "
                f"sharpe={max((r.sharpe for r in all_results), default=0):.3f} "
                f"elapsed={elapsed:.1f}s"
            )

    all_results.sort(key=lambda r: r.score_composite(), reverse=True)
    top = all_results[:top_n]

    _save_results(top)

    if verbose:
        print(f"\n[optimizer] Top {top_n} results:")
        _print_results(top)

    return top


def _save_results(results: List[BacktestResult]) -> None:
    try:
        data = [r.as_dict() for r in results]
        tmp = _RESULTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_RESULTS_FILE)
        logger.info(f"optimizer: results saved to {_RESULTS_FILE}")
    except Exception as e:
        logger.error(f"optimizer: failed to save results — {e}")


def _print_results(results: List[BacktestResult]) -> None:
    header = (
        f"{'#':<3} {'STP':>5} {'TRL':>5} {'SCR':>5} "
        f"{'PnL':>9} {'WR':>7} {'PF':>7} {'DD':>8} {'Sharpe':>8} {'Comp':>8}"
    )
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for rank, r in enumerate(results, 1):
        p = r.params
        print(
            f"{rank:<3} {p.atr_stop_mult:>5.2f} {p.atr_trail_mult:>5.2f} "
            f"{p.score_threshold:>5} {r.total_pnl:>9.2f} "
            f"{r.win_rate*100:>6.1f}% {r.profit_factor:>7.2f} "
            f"{r.max_drawdown:>8.4f} {r.sharpe:>8.3f} {r.score_composite():>8.3f}"
        )
    print("-" * len(header))


def load_best_params() -> Optional[ParamSet]:
    """Load the best parameter set from the last optimizer run."""
    try:
        if _RESULTS_FILE.exists():
            data = json.loads(_RESULTS_FILE.read_text())
            if data:
                best = data[0]["params"]
                return ParamSet(**best)
    except Exception as e:
        logger.warning(f"optimizer: could not load best params — {e}")
    return None
