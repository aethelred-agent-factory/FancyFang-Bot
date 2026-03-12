import os
import sys
import unittest
from pathlib import Path
import sqlite3
import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research.build_sequences import build_sequences
import core.phemex_common as pc

class DummyCandleProvider:
    """Simple stub to replace pc.get_candles in tests."""
    @staticmethod
    def get_candles(symbol, timeframe="1H", limit=500):
        # build sequential candles ending at current time
        now = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
        candles = []
        for i in range(limit):
            ts = now - (limit - i) * 3600 * 1000  # 1h spacing
            candles.append([int(ts), timeframe, 1.0, 2.0, 0.5, 1.5, 100.0])
        return candles

class TestSequencePipeline(unittest.TestCase):
    def setUp(self):
        # create a temporary database and insert a fake trade
        self.db_path = Path("test_seq.db")
        if self.db_path.exists():
            self.db_path.unlink()
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                direction TEXT,
                entry REAL,
                exit REAL,
                pnl REAL,
                hold_time_s INTEGER,
                score REAL,
                reason TEXT,
                timestamp TEXT,
                signals_json TEXT,
                slippage REAL,
                raw_signals_json TEXT,
                narrative TEXT,
                tags_json TEXT,
                primary_driver TEXT,
                failure_mode TEXT,
                market_context_json TEXT,
                ml_features_json TEXT
            )
        """)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        c.execute(
            "INSERT INTO trade_history (symbol, direction, entry, exit, pnl, timestamp) VALUES (?,?,?,?,?,?)",
            ("BTCUSDT", "LONG", 1.0, 2.0, 5.0, ts),
        )
        conn.commit()
        conn.close()

        # patch the candle fetcher
        self.orig_get_candles = pc.get_candles
        pc.get_candles = DummyCandleProvider.get_candles

    def tearDown(self):
        pc.get_candles = self.orig_get_candles
        if self.db_path.exists():
            self.db_path.unlink()

    def test_build_sequences_basic(self):
        X, y, tids, syms = build_sequences(self.db_path, timeframe="1H", seq_len=10)
        self.assertEqual(X.shape[0], 1)
        self.assertEqual(X.shape[1], 10)
        self.assertEqual(y.tolist(), [1])
        self.assertEqual(syms.tolist(), ["BTCUSDT"])

if __name__ == "__main__":
    unittest.main()
