"""Performance monitoring subsystem for tracking trade-level metrics.

Provides comprehensive tracking of win/loss rates, PnL series, drawdown calculations,
and other key performance indicators for the trading system.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class PerformanceMonitor:
    def __init__(self, initial_balance: float = 1000.0):
        # internal state for accumulated statistics
        self.trades: List[Dict[str, Any]] = []
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.win_count = 0
        self.loss_count = 0
        self.total_pnl = 0.0
        self.pnl_series: List[float] = []
        self.peak_balance = initial_balance
        self.current_drawdown = 0.0
        self.max_drawdown = 0.0

    def record_trade(self, trade_record: Dict[str, Any]) -> None:
        """Add a new closed trade to the performance ledger.

        ``trade_record`` is expected to contain at least a 'pnl' key with the profit/loss amount.
        Updates cumulative metrics, drawdown calculations, win/loss counters, etc.
        """
        self.trades.append(trade_record)
        pnl = trade_record.get('pnl', 0.0)
        self.total_pnl += pnl
        self.pnl_series.append(pnl)
        self.current_balance += pnl

        if pnl > 0:
            self.win_count += 1
        else:
            self.loss_count += 1

        # Update drawdown
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance
            self.current_drawdown = 0.0
        else:
            self.current_drawdown = (self.peak_balance - self.current_balance) / self.peak_balance
            if self.current_drawdown > self.max_drawdown:
                self.max_drawdown = self.current_drawdown

    def get_summary(self) -> Dict[str, Any]:
        """Return a dictionary summarising current performance.

        Includes win_rate, avg_pnl, max_drawdown, total_trades, etc.
        """
        total_trades = len(self.trades)
        win_rate = self.win_count / total_trades if total_trades > 0 else 0.0
        avg_pnl = self.total_pnl / total_trades if total_trades > 0 else 0.0

        return {
            "total_trades": total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": win_rate,
            "total_pnl": self.total_pnl,
            "avg_pnl": avg_pnl,
            "current_balance": self.current_balance,
            "max_drawdown": self.max_drawdown,
            "current_drawdown": self.current_drawdown,
        }


# convenience global instance (optional)
performance_monitor = PerformanceMonitor()
