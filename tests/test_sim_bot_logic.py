import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import logging
from unittest.mock import MagicMock, patch

import core.sim_bot as sim_bot
import pytest
from core.sim_bot import SimBotState


@pytest.fixture
def mock_storage():
    with patch("core.sim_bot.StorageManager") as mock:
        yield mock.return_value


def test_get_sim_free_margin():
    balance = 100.0
    positions = [{"margin": 10.0}, {"margin": 20.0}]
    assert sim_bot.get_sim_free_margin(balance, positions) == 70.0


def test_pick_sim_leverage():
    # High ATR -> Low leverage
    assert sim_bot.pick_sim_leverage(5.0) == 5
    # Low ATR -> High leverage
    assert sim_bot.pick_sim_leverage(0.5) == 30
    # Vol spike -> lower leverage
    assert sim_bot.pick_sim_leverage(1.0, vol_spike=3.0) == 5  # 1.0 + 5 = 6.0 ATR -> 5x


def test_calculate_dynamic_cooldown():
    # Win -> short cooldown
    assert sim_bot._calculate_dynamic_cooldown(10.0, 0) == sim_bot.BASE_COOLDOWN_WIN_S

    # Loss -> longer cooldown
    # pnl = -10.0, entropy = 0
    # loss_penalty = 10 * 72 = 720
    # cooldown = 1800 + 720 = 2520
    assert sim_bot._calculate_dynamic_cooldown(-10.0, 0) == 2520

    # Loss with entropy reduction
    # reduction = 10 * 120 = 1200
    # 2520 - 1200 = 1320
    assert sim_bot._calculate_dynamic_cooldown(-10.0, 10) == 1320


def test_sim_bot_state_load_save(tmp_path, monkeypatch):
    # Redirect paper account file to temporary location for the duration of
    # the test so we don't collide with the real JSON on disk.
    json_path = tmp_path / "paper_account.json"
    monkeypatch.setattr(sim_bot, "PAPER_ACCOUNT_FILE", json_path)

    # Setup state with a custom storage path (storage isn't used for account)
    db_path = tmp_path / "test_sim_bot.db"
    storage = sim_bot.StorageManager(db_path)
    state = SimBotState(storage=storage)

    state.balance = 500.0
    state.positions = [
        {
            "symbol": "BTCUSDT",
            "margin": 50.0,
            "side": "Buy",
            "size": 0.01,
            "entry": 50000.0,
        }
    ]

    state.save_account()

    # New state, load it (should read from JSON)
    state2 = SimBotState(storage=storage)
    state2.load_account()

    assert state2.balance == 500.0
    assert len(state2.positions) == 1
    assert state2.positions[0]["symbol"] == "BTCUSDT"


@patch("core.sim_bot.state")
@patch("core.sim_bot.hw.bridge.signal")
@patch("core.sim_bot.send_telegram_message")
@patch("core.sim_bot._log_closed_trade")
def test_check_stops_live_tp_hit(mock_log, mock_tg, mock_hw, mock_state):
    # Mock position
    pos = {
        "symbol": "BTCUSDT",
        "side": "Buy",
        "entry": 100.0,
        "size": 1.0,
        "margin": 10.0,
        "stop_price": 95.0,
        "take_profit": 110.0,
        "high_water": 100.0,
    }
    mock_state.positions = [pos]
    mock_state.live_prices = {"BTCUSDT": 110.5}  # Above TP
    mock_state.balance = 1000.0

    sim_bot._check_stops_live("BTCUSDT")

    # Position should be removed
    assert len(mock_state.positions) == 0
    # Balance should be updated: 1000 + 10 (margin) + 10 (pnl) = 1020
    # Wait, pnl is (exit_price - entry) * size = (110 - 100) * 1 = 10.
    assert mock_state.balance == 1020.0
    # HW signal TP
    mock_hw.assert_called_with("TP")


def test_log_closed_trade_warning(tmp_path, caplog, monkeypatch):
    """If a close record has an unknown direction or score zero we log details."""
    # set up a fake state with one position to exercise the tracing logic
    fake_storage = MagicMock()
    fake_storage.load_account.return_value = {
        "balance": 100.0,
        "positions": [{"symbol": "FOO"}],
    }
    fake_state = sim_bot.SimBotState(storage=fake_storage)
    fake_state.positions = [{"symbol": "FOO", "side": "Buy"}]
    monkeypatch.setattr(sim_bot, "state", fake_state)

    caplog.set_level(logging.WARNING)
    # call with both anomalies at once
    sim_bot._log_closed_trade(
        symbol="BAR",
        direction="Unknown",
        entry=1.0,
        exit_price=2.0,
        size=1.0,
        entry_score=0,
        entry_time=None,
        reason="test",
    )

    assert "Suspicious close record" in caplog.text
    assert "Current sim positions" in caplog.text


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
    monkeypatch.setattr(sim_bot, "_retrain_models_async", fake_retrain)

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
    sim_state2 = sim_bot.SimBotState(storage=sim_bot.StorageManager(db_path))
    monkeypatch.setattr(sim_bot, "PAPER_ACCOUNT_FILE", json_file)
    sim_state2.load_account()
    assert sim_state2.positions == sim_state.positions
    assert sim_state2.balance == 500.0


@patch("core.sim_bot.state")
@patch("core.sim_bot.hw.bridge.signal")
def test_check_stops_live_sl_hit(mock_hw, mock_state):
    # Mock position
    pos = {
        "symbol": "BTCUSDT",
        "side": "Buy",
        "entry": 100.0,
        "size": 1.0,
        "margin": 10.0,
        "stop_price": 95.0,
        "take_profit": 110.0,
        "high_water": 100.0,
    }
    mock_state.positions = [pos]
    mock_state.live_prices = {"BTCUSDT": 94.0}  # Below SL
    mock_state.balance = 1000.0

    sim_bot._check_stops_live("BTCUSDT")

    # Position should be removed
    assert len(mock_state.positions) == 0
    # Balance should be updated: 1000 + 10 (margin) - 5 (pnl) = 1005
    # Exit price is stop_price = 95.0. PnL = (95 - 100) * 1 = -5.
    assert mock_state.balance == 1005.0
    # HW signal SL
    mock_hw.assert_called_with("SL")
