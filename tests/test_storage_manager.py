import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
from pathlib import Path

from modules.storage_manager import StorageManager


class TestStorageManager(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("test_fancybot.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.storage = StorageManager(self.db_path)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_account_save_load(self):
        balance = 500.0
        positions = [
            {
                "symbol": "BTCUSDT",
                "side": "Buy",
                "size": 0.1,
                "margin": 50.0,
                "entry": 50000.0,
            }
        ]
        self.storage.save_account_state(balance, positions)

        loaded = self.storage.load_account()
        self.assertEqual(loaded["balance"], balance)
        self.assertEqual(len(loaded["positions"]), 1)
        self.assertEqual(loaded["positions"][0]["symbol"], "BTCUSDT")

    def test_trade_history(self):
        trade = {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "entry": 2000.0,
            "exit": 2100.0,
            "pnl": 10.0,
            "timestamp": "2026-03-08T20:00:00Z",
            "reason": "tp",
            "signals": ["RSI Oversold"],
        }
        self.storage.append_trade(trade)

        history = self.storage.get_trade_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["symbol"], "ETHUSDT")
        self.assertEqual(history[0]["pnl"], 10.0)
        self.assertEqual(history[0]["signals"], ["RSI Oversold"])

    def test_training_state_methods(self):
        # Initially there should be a row with zero trades
        state = self.storage.get_model_training_state()
        self.assertIn("trades_since_last_training", state)
        self.assertEqual(state["trades_since_last_training"], 0)
        self.assertIsNone(state.get("last_training_timestamp"))

        # Increment counter and verify
        self.storage.increment_trades_since_last_training()
        state = self.storage.get_model_training_state()
        self.assertEqual(state["trades_since_last_training"], 1)

        # Reset and update timestamp
        self.storage.reset_trades_since_last_training()
        state = self.storage.get_model_training_state()
        self.assertEqual(state["trades_since_last_training"], 0)
        self.storage.update_last_training_timestamp("2026-03-12T00:00:00Z")
        state = self.storage.get_model_training_state()
        self.assertEqual(state["last_training_timestamp"], "2026-03-12T00:00:00Z")

    def test_count_annotated_trades(self):
        # no annotated trades yet
        self.assertEqual(self.storage.count_annotated_trades(), 0)
        # add a trade with narrative
        trade = {"symbol": "X", "direction": "LONG", "entry": 1, "exit": 2, "pnl": 1, "timestamp": "2026-03-08T20:00:00Z", "narrative": "test"}
        self.storage.append_trade(trade)
        self.assertEqual(self.storage.count_annotated_trades(), 1)


if __name__ == "__main__":
    unittest.main()
