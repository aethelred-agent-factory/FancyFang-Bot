import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest

import core.phemex_common as pc


class TestPhemexCommonUpgrades(unittest.TestCase):

    def test_calc_slippage(self):
        # Case 1: Bid/Ask available
        # Spread = 101 - 99 = 2. Half spread = 1.
        # Factor 0.5 -> Slippage = 0.5
        # Long: 100 + 0.5 = 100.5
        fill, slip = pc.calc_slippage(
            100.0, "LONG", best_bid=99.0, best_ask=101.0, slippage_factor=0.5
        )
        self.assertAlmostEqual(fill, 100.5)
        self.assertAlmostEqual(slip, 0.5)

        # Short: 100 - 0.5 = 99.5
        fill, slip = pc.calc_slippage(
            100.0, "SHORT", best_bid=99.0, best_ask=101.0, slippage_factor=0.5
        )
        self.assertAlmostEqual(fill, 99.5)
        self.assertAlmostEqual(slip, 0.5)

        # Case 2: No Bid/Ask, use ATR
        # ATR = 10. 1% of ATR = 0.1
        fill, slip = pc.calc_slippage(100.0, "LONG", atr=10.0, slippage_factor=0.5)
        self.assertAlmostEqual(fill, 100.1)
        self.assertAlmostEqual(slip, 0.1)

    def test_calc_atr_stops(self):
        # Entry 100, ATR 10
        # Stop mult 1.5 -> Stop dist 15
        # Trail mult 1.0 -> Trail dist 10
        # Long stop = 100 - 15 = 85
        stop, trail = pc.calc_atr_stops(
            100.0, 10.0, "LONG", stop_mult=1.5, trail_mult=1.0
        )
        self.assertEqual(stop, 85.0)
        self.assertEqual(trail, 10.0)

        # Short stop = 100 + 15 = 115
        stop, trail = pc.calc_atr_stops(
            100.0, 10.0, "SHORT", stop_mult=1.5, trail_mult=1.0
        )
        self.assertEqual(stop, 115.0)
        self.assertEqual(trail, 10.0)

    def test_update_atr_trail(self):
        # LONG
        # Price moves up to 120. High water becomes 120.
        # Trail dist 10. New stop = 120 - 10 = 110.
        # Old stop 85. Max(85, 110) = 110.
        stop, hw, lw = pc.update_atr_trail(120.0, 85.0, 100.0, 100.0, 10.0, "LONG")
        self.assertEqual(stop, 110.0)
        self.assertEqual(hw, 120.0)

        # Price drops to 115. High water stays 120.
        # New stop calc = 120 - 10 = 110.
        # Stop stays 110.
        stop, hw, lw = pc.update_atr_trail(115.0, 110.0, 120.0, 100.0, 10.0, "LONG")
        self.assertEqual(stop, 110.0)
        self.assertEqual(hw, 120.0)

        # SHORT
        # Price moves down to 80. Low water becomes 80.
        # Trail dist 10. New stop = 80 + 10 = 90.
        # Old stop 115. Min(115, 90) = 90.
        stop, hw, lw = pc.update_atr_trail(80.0, 115.0, 100.0, 100.0, 10.0, "SHORT")
        self.assertEqual(stop, 90.0)
        self.assertEqual(lw, 80.0)

    def test_check_spread_filter(self):
        # Max is default 0.20%
        # Pass
        passed, reason = pc.check_spread_filter(0.10, "BTCUSDT")
        self.assertTrue(passed)
        self.assertEqual(reason, "")

        # Fail
        passed, reason = pc.check_spread_filter(0.25, "BTCUSDT")
        self.assertFalse(passed)
        self.assertIn("spread 0.2500%", reason)

    def test_calc_market_regime(self):
        # Trending: Constant percentage returns (100%) -> 0 entropy
        # 1, 2, 4, 8, 16...
        closes = [float(2**i) for i in range(30)]
        regime, entropy = pc.calc_market_regime(closes, period=20)

        # Debug info if failure
        if regime != "TRENDING":
            print(f"DEBUG: regime={regime}, entropy={entropy}")

        # Should be TRENDING because returns are identical (entropy 0)
        self.assertEqual(regime, "TRENDING")

        # Ranging/Volatile would be random noise, harder to deterministic test without seeding
        # but we can verify it returns valid strings
        import random

        random.seed(42)
        closes_random = [random.random() * 100 for _ in range(30)]
        regime, entropy = pc.calc_market_regime(closes_random, period=20)
        self.assertIn(regime, ["TRENDING", "RANGING", "VOLATILE", "UNKNOWN"])

    def test_calc_kalman_series(self):
        closes = [10.0, 11.0, 12.0, 11.0, 10.0]
        kalman = pc.calc_kalman_series(closes)
        self.assertEqual(len(kalman), len(closes))
        self.assertIsInstance(kalman[0], float)

    def test_calc_order_book_imbalance(self):
        # Bids: [[price, qty], ...]
        bids = [
            ["100", "10"],
            ["99", "10"],
        ]  # Total bid vol 100*10 + 99*10 = 1000 + 990 = 1990
        asks = [
            ["101", "5"],
            ["102", "5"],
        ]  # Total ask vol 101*5 + 102*5 = 505 + 510 = 1015

        # Ratio approx 1990 / 1015 ≈ 1.96
        ratio = pc.calc_order_book_imbalance(bids, asks, depth_levels=2)
        self.assertIsNotNone(ratio)
        self.assertAlmostEqual(ratio, 1.96, delta=0.01)

        # Empty data
        self.assertIsNone(pc.calc_order_book_imbalance([], []))


if __name__ == "__main__":
    unittest.main()
