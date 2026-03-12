"""Stub for the performance monitoring subsystem.

Steps 22--24 of the design doc call for a component that tracks trade-level
metrics (win rate, drawdown, expectancy, etc.) and exposes a simple
interface for the rest of the system.  This file provides the skeleton; the
real calculations are left as ``TODO`` placeholders until the exact
requirements are fleshed out.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class PerformanceMonitor:
    def __init__(self):
        # internal state for accumulated statistics
        self.trades: List[Dict[str, Any]] = []
        # TODO: add fields for win/loss counters, PnL series, drawdown, etc.

    def record_trade(self, trade_record: Dict[str, Any]) -> None:
        """Add a new closed trade to the performance ledger.

        ``trade_record`` is expected to be the same dictionary produced by the
        strategy modules when a position is closed.  Calling code (p_bot,
        backtest, etc.) should invoke this immediately after the trade is
        persisted.

        TODO: update cumulative metrics, recalc drawdown, win rate, etc.
        """
        self.trades.append(trade_record)
        # TODO: update derived statistics here

    def get_summary(self) -> Dict[str, Any]:
        """Return a dictionary summarising current performance.

        Example keys might include ``win_rate``, ``avg_pnl``, ``max_drawdown``
        and ``trades`` (number of records).  The exact schema is TBD.
        """
        # TODO: compute and return real metrics
        return {
            "trades": len(self.trades),
            "win_rate": None,
            "avg_pnl": None,
            "max_drawdown": None,
        }


# convenience global instance (optional)
performance_monitor = PerformanceMonitor()
