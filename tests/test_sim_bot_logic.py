import pytest
from unittest.mock import MagicMock, patch
import sim_bot
from sim_bot import SimBotState

@pytest.fixture
def mock_storage():
    with patch("sim_bot.StorageManager") as mock:
        yield mock.return_value

def test_get_sim_free_margin():
    balance = 100.0
    positions = [
        {"margin": 10.0},
        {"margin": 20.0}
    ]
    assert sim_bot.get_sim_free_margin(balance, positions) == 70.0

def test_pick_sim_leverage():
    # High ATR -> Low leverage
    assert sim_bot.pick_sim_leverage(5.0) == 5
    # Low ATR -> High leverage
    assert sim_bot.pick_sim_leverage(0.5) == 30
    # Vol spike -> lower leverage
    assert sim_bot.pick_sim_leverage(1.0, vol_spike=3.0) == 5 # 1.0 + 5 = 6.0 ATR -> 5x

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

def test_sim_bot_state_load_save(tmp_path):
    # Setup state with a custom storage path
    db_path = tmp_path / "test_sim_bot.db"
    storage = sim_bot.StorageManager(db_path)
    state = SimBotState(storage=storage)
    
    state.balance = 500.0
    state.positions = [{
        "symbol": "BTCUSDT", "margin": 50.0, "side": "Buy", "size": 0.01, "entry": 50000.0
    }]
    
    state.save_account()
    
    # New state, load it
    state2 = SimBotState(storage=storage)
    state2.load_account()
    
    assert state2.balance == 500.0
    assert len(state2.positions) == 1
    assert state2.positions[0]["symbol"] == "BTCUSDT"

@patch("sim_bot.state")
@patch("sim_bot.hw.bridge.signal")
@patch("sim_bot.send_telegram_message")
@patch("sim_bot._log_closed_trade")
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
        "high_water": 100.0
    }
    mock_state.positions = [pos]
    mock_state.live_prices = {"BTCUSDT": 110.5} # Above TP
    mock_state.balance = 1000.0
    
    sim_bot._check_stops_live("BTCUSDT")
    
    # Position should be removed
    assert len(mock_state.positions) == 0
    # Balance should be updated: 1000 + 10 (margin) + 10 (pnl) = 1020
    # Wait, pnl is (exit_price - entry) * size = (110 - 100) * 1 = 10.
    assert mock_state.balance == 1020.0
    # HW signal TP
    mock_hw.assert_called_with('TP')

@patch("sim_bot.state")
@patch("sim_bot.hw.bridge.signal")
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
        "high_water": 100.0
    }
    mock_state.positions = [pos]
    mock_state.live_prices = {"BTCUSDT": 94.0} # Below SL
    mock_state.balance = 1000.0
    
    sim_bot._check_stops_live("BTCUSDT")
    
    # Position should be removed
    assert len(mock_state.positions) == 0
    # Balance should be updated: 1000 + 10 (margin) - 5 (pnl) = 1005
    # Exit price is stop_price = 95.0. PnL = (95 - 100) * 1 = -5.
    assert mock_state.balance == 1005.0
    # HW signal SL
    mock_hw.assert_called_with('SL')
