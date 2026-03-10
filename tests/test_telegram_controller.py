import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
import time
from unittest.mock import patch, MagicMock
import modules.telegram_controller as telegram_controller

@pytest.fixture
def mock_requests():
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        yield mock_get, mock_post

def test_telegram_controller_send(mock_requests):
    mock_get, mock_post = mock_requests
    with (patch("modules.telegram_controller.TG_BOT_TOKEN", "token"), 
          patch("modules.telegram_controller.TG_CHAT_ID", "chat_id")):
        telegram_controller._send("Hello World")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["text"] == "Hello World"
        assert kwargs["json"]["chat_id"] == "chat_id"

def test_telegram_controller_get_updates(mock_requests):
    mock_get, mock_post = mock_requests
    mock_get.return_value.json.return_value = {
        "result": [{"update_id": 100, "message": {"text": "/status", "chat": {"id": "chat_id"}}}]
    }
    
    with patch("modules.telegram_controller.TG_BOT_TOKEN", "token"):
        updates = telegram_controller._get_updates()
        assert len(updates) == 1
        assert telegram_controller._offset == 101

def test_handle_start_stop():
    telegram_controller._handle_start("chat_id")
    assert telegram_controller.is_halted() is False
    
    telegram_controller._handle_stop("chat_id")
    assert telegram_controller.is_halted() is True

def test_handle_status(mock_requests):
    mock_get, mock_post = mock_requests
    telegram_controller._get_balance = lambda: 1000.0
    telegram_controller._get_positions = lambda: [{}, {}]
    
    with (patch("modules.drawdown_guard.get_status", return_value={
            "daily_pnl": 10.0, "loss_pct": 0.01, "killed": False, "remaining": 90.0
          }),
          patch("modules.telegram_controller.TG_BOT_TOKEN", "token"),
          patch("modules.telegram_controller.TG_CHAT_ID", "chat_id")):
        telegram_controller._handle_status("chat_id")
        mock_post.assert_called()
        text = mock_post.call_args[1]["json"]["text"]
        assert "Balance: `1000.0000`" in text
        assert "Open positions: `2`" in text

def test_handle_profit(mock_requests):
    mock_get, mock_post = mock_requests
    telegram_controller._get_session_pnl = lambda: {"wins": 2, "losses": 1, "total_pnl": 15.0}
    
    with (patch("modules.telegram_controller.TG_BOT_TOKEN", "token"),
          patch("modules.telegram_controller.TG_CHAT_ID", "chat_id")):
        telegram_controller._handle_profit("chat_id")
        text = mock_post.call_args[1]["json"]["text"]
        assert "Win rate: `66.7%`" in text
        assert "Total PnL: `+15.0000`" in text

def test_handle_positions(mock_requests):
    mock_get, mock_post = mock_requests
    telegram_controller._get_positions = lambda: [
        {"symbol": "BTCUSDT", "side": "Buy", "entry": 50000, "pnl": 100, "entry_score": 150}
    ]
    
    with (patch("modules.telegram_controller.TG_BOT_TOKEN", "token"),
          patch("modules.telegram_controller.TG_CHAT_ID", "chat_id")):
        telegram_controller._handle_positions("chat_id")
        text = mock_post.call_args[1]["json"]["text"]
        assert "BTCUSDT" in text
        assert "PnL: `+100.0000`" in text

def test_poll_loop_iteration(mock_requests):
    mock_get, mock_post = mock_requests
    # Mock one update then stop the loop
    mock_get.return_value.json.return_value = {
        "result": [{"update_id": 100, "message": {"text": "/start", "chat": {"id": "chat_id"}}}]
    }
    
    with (patch("modules.telegram_controller.TG_CHAT_ID", "chat_id"), 
          patch("modules.telegram_controller.TG_BOT_TOKEN", "token")):
        
        telegram_controller._running = True
        # We need to stop it after one iteration. 
        # Since it sleeps 1s, we can use a thread and then stop it.
        import threading
        t = threading.Thread(target=telegram_controller._poll_loop)
        t.start()
        time.sleep(0.5)
        telegram_controller.stop()
        t.join()
        
        assert telegram_controller.is_halted() is False
