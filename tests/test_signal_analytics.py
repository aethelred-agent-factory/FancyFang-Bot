import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import modules.signal_analytics as signal_analytics

@pytest.fixture
def temp_analytics_file(tmp_path):
    """Fixture to provide a temporary analytics file path and reset signal_analytics cache."""
    test_file = tmp_path / "test_signal_analytics.json"
    
    # Patch the global _ANALYTICS_FILE and reset cache
    with patch("modules.signal_analytics._ANALYTICS_FILE", test_file):
        old_storage = signal_analytics._storage
        signal_analytics._storage = None
        signal_analytics._cache = None
        signal_analytics._dirty_count = 0
        yield test_file
        signal_analytics._storage = old_storage
        # Cleanup if needed (tmp_path handles it mostly)
        if test_file.exists():
            test_file.unlink()
        if test_file.with_suffix(".tmp").exists():
            test_file.with_suffix(".tmp").unlink()

def test_ensure_loaded_empty(temp_analytics_file):
    """Test that _ensure_loaded returns an empty dict when no file exists."""
    cache = signal_analytics._ensure_loaded()
    assert cache == {}
    assert signal_analytics._cache == {}

def test_record_trade_basic(temp_analytics_file):
    """Test recording a basic trade."""
    signal_analytics.record_trade(
        signal_types=["RSI Recovery"],
        entry_price=100.0,
        exit_price=105.0,
        pnl=5.0,
        direction="LONG",
        symbol="BTCUSDT"
    )
    
    full_stats = signal_analytics.get_signal_stats()
    stats = full_stats["signals"]
    assert "RSI Recovery" in stats
    s = stats["RSI Recovery"]
    assert s["trade_count"] == 1
    assert s["win_count"] == 1
    assert s["loss_count"] == 0
    assert s["win_rate"] == 1.0
    assert s["avg_return"] == 5.0
    assert s["expectancy"] == 5.0
    assert s["profit_factor"] == float("inf")

    log = signal_analytics.get_trade_log()
    assert len(log) == 1
    assert log[0]["symbol"] == "BTCUSDT"
    assert log[0]["pnl"] == 5.0
    assert log[0]["signal_types"] == ["RSI Recovery"]

def test_record_trade_multiple_signals(temp_analytics_file):
    """Test recording a trade with multiple signals."""
    signal_analytics.record_trade(
        signal_types=["SigA", "SigB"],
        entry_price=100.0,
        exit_price=98.0,
        pnl=-2.0,
        direction="SHORT",
        symbol="ETHUSDT"
    )
    
    full_stats = signal_analytics.get_signal_stats()
    stats = full_stats["signals"]
    for sig in ["SigA", "SigB"]:
        assert stats[sig]["trade_count"] == 1
        assert stats[sig]["loss_count"] == 1
        assert stats[sig]["avg_return"] == -2.0

def test_pnl_list_bounding(temp_analytics_file):
    """Test that pnl_list is bounded to 500 entries."""
    for i in range(510):
        signal_analytics.record_trade(["Sig"], 100, 101, 1.0, "LONG", "BTC")
    
    with signal_analytics._lock:
        cache = signal_analytics._ensure_loaded()
        assert len(cache["signals"]["Sig"]["pnl_list"]) == 500

def test_trade_log_bounding(temp_analytics_file):
    """Test that trade_log is bounded to 1000 entries."""
    for i in range(1010):
        signal_analytics.record_trade(["Sig"], 100, 101, 1.0, "LONG", "BTC")
    
    log = signal_analytics.get_trade_log()
    assert len(log) == 1000

def test_flush_mechanism(temp_analytics_file):
    """Test that cache is flushed to disk every FLUSH_EVERY trades."""
    # FLUSH_EVERY is 5 by default
    for i in range(4):
        signal_analytics.record_trade(["Sig"], 100, 101, 1.0, "LONG", "BTC")
    
    assert not temp_analytics_file.exists()
    
    signal_analytics.record_trade(["Sig"], 100, 101, 1.0, "LONG", "BTC")
    assert temp_analytics_file.exists()
    
    with open(temp_analytics_file, "r") as f:
        data = json.load(f)
        assert data["signals"]["Sig"]["trade_count"] == 5

def test_manual_flush(temp_analytics_file):
    """Test manual flush."""
    signal_analytics.record_trade(["Sig"], 100, 101, 1.0, "LONG", "BTC")
    assert not temp_analytics_file.exists()
    signal_analytics.flush()
    assert temp_analytics_file.exists()

def test_get_signal_stats_empty(temp_analytics_file):
    """Test get_signal_stats with no data."""
    assert signal_analytics.get_signal_stats() == {"signals": {}, "hours": {}}

def test_get_signal_stats_mixed(temp_analytics_file):
    """Test complex stats calculation."""
    # 2 wins of 10, 1 loss of 5 -> Win rate 66.6%, Avg return 5, Profit factor 4, Expectancy 5
    signal_analytics.record_trade(["Sig"], 100, 110, 10.0, "LONG", "BTC")
    signal_analytics.record_trade(["Sig"], 100, 110, 10.0, "LONG", "BTC")
    signal_analytics.record_trade(["Sig"], 100, 95, -5.0, "LONG", "BTC")
    
    stats = signal_analytics.get_signal_stats()["signals"]["Sig"]
    assert stats["trade_count"] == 3
    assert stats["win_count"] == 2
    assert stats["loss_count"] == 1
    assert stats["win_rate"] == round(2/3, 4)
    assert stats["avg_return"] == round(15/3, 6)
    # expectancy = win_rate * avg_win - loss_rate * avg_loss
    # win_rate = 2/3, avg_win = 10
    # loss_rate = 1/3, avg_loss = 5
    # expectancy = (2/3)*10 - (1/3)*5 = 20/3 - 5/3 = 15/3 = 5.0
    assert stats["expectancy"] == 5.0
    assert stats["profit_factor"] == 20/5 # 4.0

def test_load_corrupt_file(temp_analytics_file):
    """Test recovery from corrupt JSON file."""
    temp_analytics_file.write_text("corrupt data")
    # Should not crash, should return empty cache
    cache = signal_analytics._load()
    assert cache == {}

def test_thread_safety(temp_analytics_file):
    """Basic thread safety test."""
    import threading
    
    def worker():
        for _ in range(50):
            signal_analytics.record_trade(["Sig"], 100, 101, 1.0, "LONG", "BTC")
            
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    stats = signal_analytics.get_signal_stats()["signals"]
    assert stats["Sig"]["trade_count"] == 250

def test_print_report_no_data(temp_analytics_file, capsys):
    """Test print_signal_report with no data."""
    signal_analytics.print_signal_report()
    captured = capsys.readouterr()
    assert "No signal analytics data yet." in captured.out

def test_print_report_with_data(temp_analytics_file, capsys):
    """Test print_signal_report with data."""
    signal_analytics.record_trade(["SigA"], 100, 110, 10.0, "LONG", "BTC")
    signal_analytics.flush()
    signal_analytics.print_signal_report()
    captured = capsys.readouterr()
    assert "Signal Performance Report" in captured.out
    assert "SigA" in captured.out
