import datetime
import json
import logging
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import core.sim_bot as sim_bot


def test_log_closed_trade_pnl_buy():
    # Mock state and storage
    fake_storage = MagicMock()
    fake_storage.append_trade.return_value = 1
    # ensure we have the correct counter state
    fake_storage.get_model_training_state.return_value = {"trades_since_last_training": 0}
    
    mock_state = sim_bot.SimBotState(storage=fake_storage)
    mock_state.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": 1.0, "entry": 50000.0}]
    
    with patch("core.sim_bot.state", mock_state):
        sim_bot._log_closed_trade(
            symbol="BTCUSDT",
            direction="Buy",
            entry=50000.0,
            exit_price=51000.0,
            size=1.0,
            entry_score=150,
            entry_time="2026-03-08T12:00:00Z",
            reason="tp",
        )
        
        # Verify append_trade was called with correct pnl
        # pnl = (51000 - 50000) * 1.0 = 1000.0
        args, _ = fake_storage.append_trade.call_args
        record = args[0]
        assert record["pnl"] == 1000.0
        assert record["direction"] == "LONG"


def test_log_closed_trade_pnl_sell():
    fake_storage = MagicMock()
    fake_storage.append_trade.return_value = 1
    fake_storage.get_model_training_state.return_value = {"trades_since_last_training": 0}
    
    mock_state = sim_bot.SimBotState(storage=fake_storage)
    mock_state.positions = [{"symbol": "BTCUSDT", "side": "Sell", "size": 1.0, "entry": 50000.0}]
    
    with patch("core.sim_bot.state", mock_state):
        sim_bot._log_closed_trade(
            symbol="BTCUSDT",
            direction="Sell",
            entry=50000.0,
            exit_price=49000.0,
            size=1.0,
            entry_score=150,
            entry_time="2026-03-08T12:00:00Z",
            reason="tp",
        )
        
        # pnl = (50000 - 49000) * 1.0 = 1000.0
        args, _ = fake_storage.append_trade.call_args
        record = args[0]
        assert record["pnl"] == 1000.0
        assert record["direction"] == "SHORT"


def test_log_closed_trade_slippage():
    fake_storage = MagicMock()
    fake_storage.append_trade.return_value = 1
    fake_storage.get_model_training_state.return_value = {"trades_since_last_training": 0}
    
    mock_state = sim_bot.SimBotState(storage=fake_storage)
    
    with patch("core.sim_bot.state", mock_state):
        sim_bot._log_closed_trade(
            symbol="BTCUSDT",
            direction="Buy",
            entry=50000.0,
            exit_price=51000.0,
            size=1.0,
            entry_score=150,
            entry_time=None,
            reason="tp",
            slippage=0.05
        )
        args, _ = fake_storage.append_trade.call_args
        assert args[0]["slippage"] == 0.05


def test_update_pnl_and_stops_ratchet(tmp_path):
    # Setup sim_bot with a temporary paper account file
    paper_file = tmp_path / "paper_account.json"
    sim_bot.PAPER_ACCOUNT_FILE = paper_file
    
    fake_storage = MagicMock()
    # Mock load_account to return initial balance
    fake_storage.load_account.return_value = {"balance": 1000.0, "positions": []}
    
    state = sim_bot.SimBotState(storage=fake_storage)
    # Open a Long position
    state.positions = [{
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": 0.1,
        "entry": 50000.0,
        "margin": 50.0,
        "leverage": 10,
        "stop_loss": 48000.0,
        "take_profit": 55000.0,
        "trail_pct": 2.0,
        "highest_price": 50000.0,
        "entry_score": 150,
        "entry_time": "2026-03-08T12:00:00Z"
    }]
    
    # Mock current price moves up to 52000
    prices = {"BTCUSDT": 52000.0}
    
    with patch("core.sim_bot.state", state):
        sim_bot.update_pnl_and_stops(prices)
        
        pos = state.positions[0]
        assert pos["highest_price"] == 52000.0
        # New stop should be 52000 * (1 - 0.02) = 50960.0
        assert pos["stop_loss"] == 50960.0


def test_update_pnl_and_stops_close_sl():
    fake_storage = MagicMock()
    # Mock append_trade to verify it's called on close
    fake_storage.append_trade.return_value = 1
    fake_storage.get_model_training_state.return_value = {"trades_since_last_training": 0}
    
    state = sim_bot.SimBotState(storage=fake_storage)
    state.positions = [{
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": 1.0,
        "entry": 50000.0,
        "stop_loss": 50500.0, # Stop loss above entry (trailed)
        "take_profit": 55000.0,
        "highest_price": 51000.0,
        "entry_score": 150,
        "entry_time": None
    }]
    
    # Price hits stop loss
    prices = {"BTCUSDT": 50400.0}
    
    with patch("core.sim_bot.state", state):
        sim_bot.update_pnl_and_stops(prices)
        assert len(state.positions) == 0
        assert fake_storage.append_trade.called


def test_retrain_trigger(monkeypatch):
    """When enough annotated trades accumulate, retrain_models_async should be scheduled."""
    fake_storage = MagicMock()
    fake_storage.append_trade.return_value = 1
    fake_storage.increment_trades_since_last_training = MagicMock()
    fake_storage.count_annotated_trades.return_value = 250
    fake_storage.get_model_training_state.return_value = {"trades_since_last_training": 50}
    monkeypatch.setattr(sim_bot, "state", sim_bot.SimBotState(storage=fake_storage))

    # patch the asynchronous retrain function so it doesn't actually run
    called = False
    def fake_retrain():
        nonlocal called
        called = True
    monkeypatch.setattr(sim_bot, "retrain_models_async", fake_retrain)

    # Patch threading.Thread to execute immediately for the test
    class MockThread:
        def __init__(self, target, args=(), kwargs={}):
            self.target = target
            self.args = args
            self.kwargs = kwargs
        def start(self):
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(sim_bot.threading, "Thread", MockThread)

    # Patch narrator to return a successful result to trigger the increment
    monkeypatch.setattr(sim_bot.narrator, "narrate_closed_trade", MagicMock(return_value={"confidence": 0.95}))

    # call the log_closed_trade with minimal params
    sim_bot._log_closed_trade(
        symbol="X",
        direction="Buy",
        entry=1.0,
        exit_price=2.0,
        size=1.0,
        entry_score=10,
        entry_time=None,
        reason="tp",
    )
    assert called, "Retrain should have been triggered when threshold met"


def test_sim_account_isolation(tmp_path, monkeypatch, caplog):
    """Live p_bot positions stored in the shared DB must not appear in the sim account."""
    # point sim to a temporary JSON file
    json_file = tmp_path / "paper_account.json"
    monkeypatch.setattr(sim_bot, "PAPER_ACCOUNT_FILE", json_file)

    # write a "live" position directly into the database used by p_bot
    db_path = tmp_path / "shared.db"
    p_storage = sim_bot.StorageManager(db_path)
    p_storage.save_account_state(
        1000.0,
        [
            {
                "symbol": "LIVE",
                "side": "Buy",
                "size": 1.0,
                "margin": 10.0,
                "entry": 100.0,
            }
        ],
    )

    # now create a new sim state that points at the same DB but will read only the JSON
    sim_state = sim_bot.SimBotState(storage=sim_bot.StorageManager(db_path))
    # ensure no JSON exists so load_account falls back to empty
    if json_file.exists():
        json_file.unlink()

    caplog.set_level(logging.WARNING)
    sim_state.load_account()
    # we expect a warning about ignoring DB positions
    assert "Shared DB contains positions" in caplog.text
    assert sim_state.positions == []
    assert sim_state.balance == sim_bot.INITIAL_BALANCE

    # write some legitimate sim data and make sure it persists to JSON
    sim_state.balance = 500.0
    sim_state.positions = [
        {"symbol": "SIM", "side": "Sell", "size": 2.0, "margin": 20.0, "entry": 200.0}
    ]
    sim_state.save_account()
    # future loads should reflect the JSON file
    assert json_file.exists()
