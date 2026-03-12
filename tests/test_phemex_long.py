import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest

import core.phemex_long as pl
from core.phemex_common import TickerData


class TestPhemexLong(unittest.TestCase):
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

    def test_score_long_neutral(self):
        score, signals = pl.score_long(self.base_data)
        # Neutral data should have near zero score, maybe some small adjustments
        # BB pct is 0.5 (at mid) -> score += 0
        # RSI 50 (neutral) -> score += 0
        # Change 0 (neutral) -> score += 0
        # Funding 0 (neutral) -> score -= 12 (positive funding 0 > 0.05 is false, < -0.01 false)
        # Wait, funding is 0.0.
        # Logic:
        # fr_pct = 0.0
        # if fr_pct < -0.10: ...
        # elif fr_pct < -0.05: ...
        # elif fr_pct < -0.01: ...
        # elif fr_pct > 0.05: ...
        # else: nothing.
        # So score should be around 0.
        self.assertTrue(abs(score) < 20, f"Score {score} too high for neutral data")

    def test_score_long_bullish(self):
        # Construct a bullish setup
        data = self.base_data
        data.rsi = 30.0  # Oversold (+25ish)
        data.prev_rsi = 29.0  # Turning up
        data.price = 48500.0  # Below BB lower (49000)
        data.bb = {
            "upper": 51000,
            "lower": 49000,
            "mid": 50000,
            "width_pct": 4.0,
            "std": 500,
        }
        # bb_pct = (48500 - 49000) / 2000 = -0.25 -> < 0.10 -> +30
        data.ema21 = 50000.0  # Price below EMA
        # pct_diff = (48500-50000)/50000 = -3% -> < -3.0 -> +15
        data.funding_rate = -0.0002  # -0.02% -> < -0.01 -> +8
        data.has_div = True  # +20

        score, signals = pl.score_long(data)
        self.assertGreater(score, 50, f"Score {score} should be high for bullish setup")
        self.assertTrue(any("Oversold" in s or "oversold" in s for s in signals))
        self.assertTrue(any("Funding" in s for s in signals))
        self.assertTrue(any("Divergence" in s for s in signals))

    def test_detect_bullish_divergence(self):
        # Price makes lower low, RSI makes higher low
        # Need enough data > DIVERGENCE_WINDOW (60)
        # Fill with neutral data
        closes = [100.0] * 60
        rsi = [50.0] * 60

        # Append the divergence pattern
        # Troughs at index -6 and -2
        # ... 100, 90, 95, 95, 95, 85, 90
        # ... 50,  30, 40, 40, 40, 35, 45
        closes.extend([100.0, 90.0, 95.0, 95.0, 95.0, 85.0, 90.0])
        rsi.extend([50.0, 30.0, 40.0, 40.0, 40.0, 35.0, 45.0])

        # P1=90, P2=85 (Lower)
        # R1=30, R2=35 (Higher) -> Divergence
        # Needs 35 < 35+10 (45) which is true.

        is_div = pl.detect_bullish_divergence(closes, rsi)
        self.assertTrue(is_div)

    def test_detect_patterns_hammer(self):
        # Hammer: long lower wick, small body, small upper wick
        # Open=100, High=101, Low=90, Close=102 (Bullish body 2, Lower wick 10, Upper wick 1)
        ohlc = [
            (105.0, 106.0, 104.0, 105.0),  # dummy
            (105.0, 106.0, 104.0, 105.0),  # dummy
            (100.0, 101.0, 90.0, 102.0),  # Hammer
        ]
        patterns = pl.detect_patterns(ohlc)
        found = any("Hammer" in p[0] for p in patterns)
        self.assertTrue(found, f"Hammer not detected in {patterns}")


if __name__ == "__main__":
    unittest.main()
