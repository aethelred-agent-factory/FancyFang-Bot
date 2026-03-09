import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
import threading
import time
import modules.drawdown_guard as drawdown_guard

def test_drawdown_guard_singleton_wrappers():
    """Test the module-level singleton wrapper functions."""
    drawdown_guard.force_reset(1000.0)
    status = drawdown_guard.get_status()
    assert status["start_balance"] == 1000.0
    
    drawdown_guard.record_pnl(-10.0, 990.0)
    status = drawdown_guard.get_status()
    assert status["daily_pnl"] == -10.0
    
    ok, reason = drawdown_guard.can_open_trade(990.0)
    assert ok is True
    
    # Hit drawdown (5% of 1000 is 50)
    drawdown_guard.record_pnl(-41.0, 949.0)
    ok, reason = drawdown_guard.can_open_trade(949.0)
    assert ok is False
    assert "Daily loss" in reason

def test_drawdown_guard_thread_safety():
    """Test thread safety of record_pnl."""
    guard = drawdown_guard.DrawdownGuard(max_drawdown=0.5)
    guard.set_start_balance(1000.0)
    
    def worker():
        for _ in range(100):
            guard.record_pnl(1.0, 1100.0)
            
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    status = guard.get_status()
    assert status["daily_pnl"] == 1000.0

def test_drawdown_guard_zero_balance():
    """Test behavior with zero start balance."""
    guard = drawdown_guard.DrawdownGuard()
    # Should not crash and should not kill
    guard.record_pnl(-10.0, 0.0)
    ok, _ = guard.can_open_trade(0.0)
    assert ok is True 
    
    status = guard.get_status()
    assert status["start_balance"] == 0.0
    assert status["loss_pct"] == 0.0
