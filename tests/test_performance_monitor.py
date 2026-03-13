"""Tests for performance_monitor.py"""

import pytest
from modules.performance_monitor import PerformanceMonitor


class TestPerformanceMonitor:
    def test_initial_state(self):
        monitor = PerformanceMonitor(initial_balance=1000.0)
        assert monitor.current_balance == 1000.0
        assert monitor.win_count == 0
        assert monitor.loss_count == 0
        assert monitor.total_pnl == 0.0
        assert len(monitor.trades) == 0

    def test_record_winning_trade(self):
        monitor = PerformanceMonitor(initial_balance=1000.0)
        trade = {"pnl": 50.0, "symbol": "BTCUSDT"}
        monitor.record_trade(trade)

        assert len(monitor.trades) == 1
        assert monitor.win_count == 1
        assert monitor.loss_count == 0
        assert monitor.total_pnl == 50.0
        assert monitor.current_balance == 1050.0
        assert monitor.peak_balance == 1050.0
        assert monitor.current_drawdown == 0.0

    def test_record_losing_trade(self):
        monitor = PerformanceMonitor(initial_balance=1000.0)
        trade = {"pnl": -30.0, "symbol": "ETHUSDT"}
        monitor.record_trade(trade)

        assert len(monitor.trades) == 1
        assert monitor.win_count == 0
        assert monitor.loss_count == 1
        assert monitor.total_pnl == -30.0
        assert monitor.current_balance == 970.0
        assert monitor.current_drawdown == 0.03  # (1000-970)/1000

    def test_drawdown_calculation(self):
        monitor = PerformanceMonitor(initial_balance=1000.0)

        # Win
        monitor.record_trade({"pnl": 100.0})
        assert monitor.peak_balance == 1100.0
        assert monitor.max_drawdown == 0.0

        # Loss
        monitor.record_trade({"pnl": -50.0})
        assert monitor.current_balance == 1050.0
        assert monitor.current_drawdown == pytest.approx(0.04545, abs=1e-5)  # approx (1100-1050)/1100
        assert monitor.max_drawdown == pytest.approx(0.04545, abs=1e-5)

        # Another loss
        monitor.record_trade({"pnl": -30.0})
        assert monitor.current_drawdown == pytest.approx(0.07272, abs=1e-5)
        assert monitor.max_drawdown == pytest.approx(0.07272, abs=1e-5)  # Updated to larger drawdown

    def test_get_summary(self):
        monitor = PerformanceMonitor(initial_balance=1000.0)
        monitor.record_trade({"pnl": 50.0})
        monitor.record_trade({"pnl": -20.0})
        monitor.record_trade({"pnl": 30.0})

        summary = monitor.get_summary()
        assert summary["total_trades"] == 3
        assert summary["win_count"] == 2
        assert summary["loss_count"] == 1
        assert summary["win_rate"] == 2/3
        assert summary["total_pnl"] == 60.0
        assert summary["avg_pnl"] == 20.0
        assert summary["current_balance"] == 1060.0
        assert summary["max_drawdown"] > 0  # From the loss

    def test_empty_summary(self):
        monitor = PerformanceMonitor()
        summary = monitor.get_summary()
        assert summary["total_trades"] == 0
        assert summary["win_rate"] == 0.0
        assert summary["avg_pnl"] == 0.0