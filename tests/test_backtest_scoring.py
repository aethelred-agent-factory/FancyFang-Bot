import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os
import sys
import unittest

# Add the root directory to sys.path to import research.backtest as backtest
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from research.backtest import score_window_unified


class TestBacktestScoring(unittest.TestCase):
    def setUp(self):
        # 100 periods of neutral data
        # Window format: (open, high, low, close, volume)
        self.neutral_window = [(100.0, 101.0, 99.0, 100.0, 1000.0)] * 101

    def test_neutral_scoring(self):
        # In a perfectly flat market, score should be low or zero
        l_score, l_sigs, *rest = score_window_unified("BTCUSDT", self.neutral_window, "LONG")
        s_score, s_sigs, *rest = score_window_unified("BTCUSDT", self.neutral_window, "SHORT")
        self.assertLess(l_score, 50)
        self.assertLess(s_score, 50)

    def test_long_oversold_rsi(self):
        # Create a price drop to trigger oversold RSI
        closes = [100.0] * 80 + [
            90.0,
            80.0,
            70.0,
            60.0,
            50.0,
            40.0,
            30.0,
            25.0,
            20.0,
            15.0,
        ]
        # Window format: (open, high, low, close, volume)
        window = [(c, c + 1, c - 1, c, 1000.0) for c in closes]

        l_score, l_sigs, *rest = score_window_unified("BTCUSDT", window, "LONG")
        # Should have rsi_oversold signal
        self.assertTrue(any("RSI" in s and "Oversold" in s for s in l_sigs))
        self.assertGreater(l_score, 20)

    def test_short_overbought_rsi(self):
        # Create a price pump
        closes = [100.0] * 80 + [110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0]
        # Window format: (open, high, low, close, volume)
        window = [(c, c + 1, c - 1, c, 1000.0) for c in closes]

        s_score, s_sigs, *rest = score_window_unified("BTCUSDT", window, "SHORT")
        # Should have rsi_overbought signal
        self.assertTrue(any("RSI" in s and "Overbought" in s for s in s_sigs))
        self.assertGreater(s_score, 20)

    def test_funding_signals(self):
        # Negative funding should boost long score
        l_score_base, _, *rest = score_window_unified(
            "BTCUSDT", self.neutral_window, "LONG", funding=0.0
        )
        l_score_neg, l_sigs, *rest = score_window_unified(
            "BTCUSDT", self.neutral_window, "LONG", funding=-0.001  # -0.1%
        )
        self.assertGreater(l_score_neg, l_score_base)
        self.assertTrue(any("Negative Funding" in s for s in l_sigs))


if __name__ == "__main__":
    unittest.main()
