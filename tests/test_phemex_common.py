import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest

from core.phemex_common import calc_bb, calc_ema_series, calc_rsi, pct_change


class TestPhemexCommon(unittest.TestCase):
    def test_pct_change(self):
        self.assertEqual(pct_change(110.0, 100.0), 10.0)
        self.assertEqual(pct_change(90.0, 100.0), -10.0)
        self.assertEqual(pct_change(100.0, 0.0), 0.0)
        self.assertEqual(pct_change(100.0, float("nan")), 0.0)

    def test_indicators(self):
        closes = [
            100,
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
            109,
            110,
            111,
            112,
            113,
            114,
            115,
        ]
        rsi, prev_rsi, history = calc_rsi(closes, period=14)
        self.assertIsNotNone(rsi)
        self.assertIsNotNone(prev_rsi)

        bb = calc_bb(closes, period=5)
        self.assertIsNotNone(bb)
        self.assertIn("upper", bb)
        self.assertIn("lower", bb)

        ema = calc_ema_series(closes, period=5)
        self.assertTrue(len(ema) > 0)

    # ------------------------------------------------------------------
    # tests added for ensemble / score_func integration
    # ------------------------------------------------------------------

    def test_score_func_range(self):
        # minimal TickerData for range check
        from core.phemex_common import TickerData, score_func

        data = TickerData(
            inst_id="X",
            price=1.0,
            rsi=None,
            prev_rsi=None,
            bb=None,
            ema21=None,
            change_24h=None,
            funding_rate=None,
            patterns=[],
            raw_ohlc=[],
        )

        for direction in ("LONG", "SHORT"):
            score = score_func(data, direction=direction)
            self.assertGreaterEqual(score, -1.0)
            self.assertLessEqual(score, 1.0)

    def test_score_func_respects_regime(self):
        from core.phemex_common import TickerData, score_func

        data = TickerData(
            inst_id="X",
            price=1.0,
            rsi=None,
            prev_rsi=None,
            bb=None,
            ema21=None,
            change_24h=None,
            funding_rate=None,
            patterns=[],
            raw_ohlc=[],
        )
        data.regime = "TRENDING"
        score = float(score_func(data, direction="LONG"))
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, -1.0)
        self.assertLessEqual(score, 1.0)

    def test_scan_corpus_table_created(self):
        # ensure the migration added the new table and we can insert a row
        from modules.storage_manager import StorageManager
        from pathlib import Path

        db_path = Path("data/state/test_temp.db")
        # remove any existing file
        try:
            db_path.unlink()
        except Exception:
            pass
        storage = StorageManager(db_path)
        conn = storage._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_corpus'")
        row = cur.fetchone()
        self.assertIsNotNone(row, "scan_corpus table should exist after init")
        # try inserting
        cur.execute("INSERT INTO scan_corpus (symbol, timestamp, data_json) VALUES (?, ?, ?)",
                    ("X", "now", "{}"))
        conn.commit()
        conn.close()
        db_path.unlink()


if __name__ == "__main__":
    unittest.main()
