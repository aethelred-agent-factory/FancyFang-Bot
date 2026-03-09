import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import sys
import os
import time
from unittest.mock import patch
from colorama import Fore

# Add the root directory to sys.path to import project modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import core.phemex_common as pc

class TestPhemexCommonExhaustive(unittest.TestCase):
    
    def test_fmt_vol(self):
        """Tests the volume formatting utility."""
        self.assertEqual(pc.fmt_vol(1234567), "1.2M")
        self.assertEqual(pc.fmt_vol(987654), "987.7K")
        self.assertEqual(pc.fmt_vol(1234), "1.2K")
        self.assertEqual(pc.fmt_vol(123), "123.00") # The function formats to 2 decimal places
        self.assertEqual(pc.fmt_vol(0), "0.00")

    def test_grade(self):
        """Tests the score grading utility."""
        # The function returns a tuple of (letter, colorama_color)
        # We check the color, which is more robust than the letter since grade boundaries are configurable.
        self.assertEqual(pc.grade(pc.SCORE_GRADE_A)[1], Fore.GREEN)
        self.assertEqual(pc.grade(pc.SCORE_GRADE_B)[1], Fore.LIGHTGREEN_EX)
        self.assertEqual(pc.grade(pc.SCORE_GRADE_C)[1], Fore.YELLOW)
        self.assertEqual(pc.grade(pc.SCORE_GRADE_C - 1)[1], Fore.RED)

    def test_calc_dynamic_threshold(self):
        """Tests the dynamic score threshold calculation."""
        self.assertEqual(pc.calc_dynamic_threshold([], 120), 120)
        self.assertEqual(pc.calc_dynamic_threshold([100, 110, 115], 120), 120)
        
        # Function uses 90th percentile
        scores = [130, 132, 135, 140, 145, 150, 155, 160, 165, 170] # 10 scores
        # 90th percentile is at index 8 (0-based), which is 165
        self.assertEqual(pc.calc_dynamic_threshold(scores, 120), 165)
        
        scores_high = [200, 210, 220]
        # It's not clamped, numpy's percentile will calculate it.
        # np.percentile([200, 210, 220], 90) = 218
        self.assertEqual(pc.calc_dynamic_threshold(scores_high, 150), 218)

    @patch('core.phemex_common.time.sleep') # Mock sleep to speed up the test
    @patch('core.phemex_common.get_thread_session')
    def test_exponential_backoff_in_safe_request(self, mock_session, mock_sleep):
        """Tests the exponential backoff logic inside safe_request."""
        # Mock the session to return a 429 status code consistently
        mock_response = unittest.mock.Mock()
        mock_response.status_code = 429
        mock_response.headers = {}
        mock_session.return_value.request.return_value = mock_response

        pc.safe_request("GET", "http://test.com")
        
        # It should try, then backoff and retry multiple times.
        # We expect sleep to be called.
        self.assertTrue(mock_sleep.called)
        # Check that the number of requests is what we expect (initial + retries)
        self.assertGreater(mock_session.return_value.request.call_count, 1)


    def test_calc_rsi(self):
        """Tests the Relative Strength Index (RSI) calculation."""
        prices_up = list(range(100, 116)) # 16 periods of pure gains
        rsi_val, _, _ = pc.calc_rsi(prices_up, 14)
        self.assertGreater(rsi_val, 99.0) # Should be extremely high (near 100)

        prices_down = list(range(115, 100, -1)) # 16 periods of pure losses
        rsi_val, _, _ = pc.calc_rsi(prices_down, 14)
        self.assertLess(rsi_val, 1.0) # Should be extremely low (near 0)

    def test_calc_ema_series(self):
        """Tests the Exponential Moving Average (EMA) series calculation."""
        prices = [10, 11, 12, 13, 14, 15]
        ema_series = pc.calc_ema_series(prices, 5)
        # Manual calculation for EMA-5 of [10, 11, 12, 13, 14, 15]
        # SMA-5 of first 5: (10+11+12+13+14)/5 = 12
        # Multiplier = 2 / (5 + 1) = 0.333...
        # EMA_t-1 = 12
        # EMA_t = (15 - 12) * 0.333... + 12 = 13.0
        self.assertAlmostEqual(ema_series[-1], 13.0)

if __name__ == '__main__':
    unittest.main()
