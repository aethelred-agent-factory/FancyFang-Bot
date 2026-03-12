import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest

import core.phemex_short as ps
from core.phemex_common import TickerData


class TestPhemexShort(unittest.TestCase):
    def setUp(self):
        self.base_data = TickerData(
            inst_id="BTCUSDT",
            price=50000.0,
            rsi=50.0,
            prev_rsi=50.0,
            bb={
                "upper": 51000,
                "lower": 49000,
                "mid": 50000,
                "width_pct": 4.0,
                "std": 500,
            },
            ema21=50000.0,
            change_24h=0.0,
            funding_rate=0.0,
            patterns=[],
            dist_low_pct=5.0,
            dist_high_pct=5.0,
            vol_spike=1.0,
            has_div=False,
            rsi_1h=50.0,
            rsi_4h=50.0,
            fr_change=0.0,
            spread=0.01,
            dist_to_node_below=None,
            dist_to_node_above=None,
            ema_slope=0.0,
            slope_change=0.0,
            news_count=0,
            news_titles=[],
            raw_ohlc=[],
            vol_24h=10000000.0,
            regime="RANGING",
            entropy=1.0,
            kalman_slope=0.0,
        )

    def test_score_short_neutral(self):
        score, signals = ps.score_short(self.base_data)
        # Neutral data should have near zero score
        self.assertTrue(abs(score) < 20, f"Score {score} too high for neutral data")

    def test_score_short_bearish(self):
        # Construct a bearish setup
        data = self.base_data
        data.rsi = 70.0  # Overbought (> 65)
        data.prev_rsi = 71.0  # Rolling over
        data.price = 51500.0  # Above BB upper (51000)
        data.bb = {
            "upper": 51000,
            "lower": 49000,
            "mid": 50000,
            "width_pct": 4.0,
            "std": 500,
        }
        # bb_pct = (51500 - 49000) / 2000 = 1.25 -> > 0.90 -> +30
        data.ema21 = 50000.0  # Price above EMA
        # pct_diff = (51500-50000)/50000 = +3% -> > 3.0 -> +15
        data.funding_rate = 0.0002  # +0.02% -> > 0.01 -> +8
        data.has_div = True  # +20

        score, signals = ps.score_short(data)
        self.assertGreater(score, 50, f"Score {score} should be high for bearish setup")
        self.assertTrue(any("Overbought" in s or "overbought" in s for s in signals))
        self.assertTrue(any("Funding" in s for s in signals))
        self.assertTrue(any("Divergence" in s for s in signals))

    def test_detect_bearish_divergence(self):
        # Price makes higher high, RSI makes lower high
        # Need enough data > DIVERGENCE_WINDOW (60)
        # Fill with neutral data
        closes = [100.0] * 60
        rsi = [50.0] * 60

        # Append the divergence pattern
        # Peaks at index -6 and -2
        # ... 100, 110, 105, 105, 105, 115, 110
        # ... 50,  70,  60,  60,  60,  65,  55
        closes.extend([100.0, 110.0, 105.0, 105.0, 105.0, 115.0, 110.0])
        rsi.extend([50.0, 70.0, 60.0, 60.0, 60.0, 65.0, 55.0])

        # P1=110, P2=115 (Higher)
        # R1=70, R2=65 (Lower) -> Divergence
        # Needs 65 > 65-10 (55) which is true.

        is_div = ps.detect_bearish_divergence(closes, rsi)
        self.assertTrue(is_div)

    def test_detect_patterns_shooting_star(self):
        # Shooting Star: long upper wick, small body, small lower wick
        # Open=100, High=110, Low=99.9, Close=101 (Bullish body 1, Upper wick 9, Lower wick 0.1)
        # 0.1 < 1.0 * 0.4 -> True
        ohlc = [
            (95.0, 96.0, 94.0, 95.0),  # dummy
            (95.0, 96.0, 94.0, 95.0),  # dummy
            (100.0, 110.0, 99.9, 101.0),  # Shooting Star
        ]
        patterns = ps.detect_patterns(ohlc)
        found = any("Shooting Star" in p[0] for p in patterns)
        self.assertTrue(found, f"Shooting Star not detected in {patterns}")


if __name__ == "__main__":
    unittest.main()
