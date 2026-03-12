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


if __name__ == "__main__":
    unittest.main()
