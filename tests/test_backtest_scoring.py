import unittest
import sys
import os

# Add the root directory to sys.path to import backtest
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backtest import score_long_window, score_short_window

class TestBacktestScoring(unittest.TestCase):
    def setUp(self):
        # 100 periods of neutral data
        self.neutral_closes = [100.0] * 101
        self.neutral_highs = [101.0] * 101
        self.neutral_lows = [99.0] * 101
        self.neutral_vols = [1000.0] * 101

    def test_neutral_scoring(self):
        # In a perfectly flat market, score should be low or zero
        # Note: EMA slope 0 might give some small score in current logic (score += 0? no)
        # Actually, price < ema200 penalizes long by 15.
        l_score, l_sigs = score_long_window(
            self.neutral_closes, self.neutral_highs, self.neutral_lows, self.neutral_vols
        )
        s_score, s_sigs = score_short_window(
            self.neutral_closes, self.neutral_highs, self.neutral_lows, self.neutral_vols
        )
        self.assertLess(l_score, 50)
        self.assertLess(s_score, 50)

    def test_long_oversold_rsi(self):
        # Create a price drop to trigger oversold RSI
        closes = [100.0] * 80 + [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 25.0, 20.0, 15.0]
        # RSI will be very low
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        vols = [1000.0] * len(closes)

        l_score, l_sigs = score_long_window(closes, highs, lows, vols)
        # Should have rsi_oversold signal
        self.assertTrue(any("RSI" in s and "oversold" in s for s in l_sigs))
        self.assertGreater(l_score, 20)

    def test_short_overbought_rsi(self):
        # Create a price pump
        closes = [100.0] * 80 + [110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        vols = [1000.0] * len(closes)

        s_score, s_sigs = score_short_window(closes, highs, lows, vols)
        # Should have rsi_overbought signal
        self.assertTrue(any("RSI" in s and "overbought" in s for s in s_sigs))
        self.assertGreater(s_score, 20)

    def test_funding_signals(self):
        # Negative funding should boost long score
        l_score_base, _ = score_long_window(
            self.neutral_closes, self.neutral_highs, self.neutral_lows, self.neutral_vols,
            funding=0.0
        )
        l_score_neg, l_sigs = score_long_window(
            self.neutral_closes, self.neutral_highs, self.neutral_lows, self.neutral_vols,
            funding=-0.001 # -0.1%
        )
        self.assertGreater(l_score_neg, l_score_base)
        self.assertTrue(any("Negative Funding" in s for s in l_sigs))

if __name__ == '__main__':
    unittest.main()
