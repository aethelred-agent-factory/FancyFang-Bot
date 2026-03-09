import unittest
from phemex_common import pct_change, calc_rsi, calc_bb, calc_ema_series

class TestPhemexCommon(unittest.TestCase):
    def test_pct_change(self):
        self.assertEqual(pct_change(110.0, 100.0), 10.0)
        self.assertEqual(pct_change(90.0, 100.0), -10.0)
        self.assertEqual(pct_change(100.0, 0.0), 0.0)
        self.assertEqual(pct_change(100.0, float('nan')), 0.0)

    def test_indicators(self):
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115]
        rsi, prev_rsi, history = calc_rsi(closes, period=14)
        self.assertIsNotNone(rsi)
        self.assertIsNotNone(prev_rsi)

        bb = calc_bb(closes, period=5)
        self.assertIsNotNone(bb)
        self.assertIn("upper", bb)
        self.assertIn("lower", bb)

        ema = calc_ema_series(closes, period=5)
        self.assertTrue(len(ema) > 0)

if __name__ == '__main__':
    unittest.main()
