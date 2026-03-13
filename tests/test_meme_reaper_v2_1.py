import unittest
from unittest.mock import patch

import core.p_bot as pb
import core.phemex_common as pc
import core.phemex_long as pl
import core.phemex_short as ps


class TestMemeReaperV21(unittest.TestCase):
    def test_ema200_calculation_in_unified_analyse(self):
        # Mock dependencies to test unified_analyse
        ticker = {"symbol": "BTCUSDT", "lastRp": "50000", "turnoverRv": "10000000"}
        cfg = {"TIMEFRAME": "4H", "MIN_VOLUME": 1000000, "CANDLES": 500}

        # Create dummy candles (500 candles to allow EMA 200 calculation)
        candles = [
            [i, 14400, 50000, 50000, 51000, 49000, 50000, 100] for i in range(500)
        ]

        with patch("core.phemex_common.get_candles", return_value=candles), patch(
            "core.phemex_common.get_funding_rate_info",
            return_value=(0.0001, 0.0001, 0.0),
        ), patch(
            "core.phemex_common.get_order_book_with_volumes",
            return_value=(49999, 50001, 0.01, 1000, 1.0, [], []),
        ):

            # Use a dummy score function
            def dummy_score(data):
                return 100, ["Signal"]

            def dummy_patterns(ohlc):
                return []

            def dummy_div(closes, rsi):
                return False

            def dummy_conf(data, score, bb):
                return "HIGH", "green", []

            result = pc.unified_analyse(
                ticker,
                cfg,
                "SHORT",
                dummy_score,
                dummy_patterns,
                dummy_div,
                dummy_conf,
                enable_ai=False,
                enable_entity=False,
            )

            self.assertIsNotNone(result)
            self.assertIn("ema200", result)
            self.assertEqual(result["ema200"], 50000.0)

    def test_score_short_ema200_divergence(self):
        # Test score_short with price > ema200
        data = pc.TickerData(
            inst_id="BTCUSDT",
            price=55000.0,
            rsi=70.0,
            prev_rsi=71.0,
            bb={"upper": 54000.0, "lower": 50000.0, "mid": 52000.0, "width_pct": 5.0},
            ema21=52000.0,
            change_24h=5.0,
            funding_rate=0.0002,
            patterns=[],
            ema200=50000.0,  # Price is 10% above EMA 200
        )

        score, signals = ps.score_short(data)
        self.assertTrue(any("Macro Divergence" in s for s in signals))
        self.assertGreaterEqual(score, 25)  # Should include ema_stretch_200 weight

    def test_score_long_ema200_divergence(self):
        # Test score_long with price < ema200
        data = pc.TickerData(
            inst_id="BTCUSDT",
            price=45000.0,
            rsi=30.0,
            prev_rsi=29.0,
            bb={"upper": 50000.0, "lower": 46000.0, "mid": 48000.0, "width_pct": 5.0},
            ema21=48000.0,
            change_24h=-5.0,
            funding_rate=-0.0002,
            patterns=[],
            ema200=50000.0,  # Price is 10% below EMA 200
        )

        score, signals = pl.score_long(data)
        self.assertTrue(any("Macro Divergence" in s for s in signals))
        self.assertGreaterEqual(score, 25)

    @patch("time.sleep", return_value=None)
    @patch("core.phemex_common.get_tickers")
    @patch("core.phemex_short.analyse")
    def test_verify_candidate_3_steps(self, mock_analyse, mock_get_tickers, mock_sleep):
        # Mock 3 successful steps
        mock_get_tickers.return_value = [{"symbol": "BTCUSDT", "lastRp": "50000"}]
        mock_analyse.return_value = {"score": 150, "price": 50000, "spread": 0.01}

        result = pb.verify_candidate("BTCUSDT", "SHORT", 150, wait_seconds=3)

        self.assertEqual(mock_analyse.call_count, 3)
        self.assertIsNotNone(result)
        self.assertEqual(result["score"], 150)

    @patch("time.sleep", return_value=None)
    @patch("core.phemex_common.get_tickers")
    @patch("core.phemex_short.analyse")
    def test_verify_candidate_fails_on_score_drop(
        self, mock_analyse, mock_get_tickers, mock_sleep
    ):
        mock_get_tickers.return_value = [{"symbol": "BTCUSDT", "lastRp": "50000"}]
        # Step 1 & 2 pass, step 3 fails (score drops too much)
        mock_analyse.side_effect = [
            {"score": 150, "price": 50000, "spread": 0.01},
            {"score": 145, "price": 50000, "spread": 0.01},
            {"score": 100, "price": 50000, "spread": 0.01},  # 100 < 150 * 0.9
        ]

        result = pb.verify_candidate("BTCUSDT", "SHORT", 150, wait_seconds=3)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
