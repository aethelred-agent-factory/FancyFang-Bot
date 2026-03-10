import unittest
import os
import sys

# Add the root directory to sys.path to import project modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import modules.risk_manager as risk_manager

class TestLiquidityGuard(unittest.TestCase):

    def setUp(self):
        """Set up a fresh RiskManager instance."""
        self.risk_manager = risk_manager.RiskManager()
        self.balance = 1000.0
        # Adaptive Pct for $1000 balance (Large account threshold is 500)
        # Ratio = (1000-50)/(500-50) = 950/450 = 2.11 (capped at 1.0)
        # Ratio is capped at 1.0? No, let's look at the code:
        # ratio = (account_balance - _SMALL_ACCOUNT_THRESHOLD) / (
        #     _LARGE_ACCOUNT_THRESHOLD - _SMALL_ACCOUNT_THRESHOLD
        # )
        # return MAX_ACCOUNT_RISK_PCT - ratio * (MAX_ACCOUNT_RISK_PCT - MIN_ACCOUNT_RISK_PCT)
        # If balance >= LARGE (500), it returns MIN_ACCOUNT_RISK_PCT (0.005)
        # Risk amount = 1000 * 0.005 = $5.0
        self.base_risk = 5.0

    def test_no_liquidity_cap_when_depth_large(self):
        """Test that position size is not capped when liquidity is abundant."""
        # Base risk $5, stop distance 0.02 (2%).
        # Position Size = 5 / 0.02 = 250.
        # Liquidity depth $5000, 10% cap = 500.
        # 250 < 500, so no cap.
        risk_amount, position_size = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=1.0,
            stop_distance=0.02,
            available_liquidity=5000.0
        )
        self.assertAlmostEqual(risk_amount, 5.0)
        self.assertAlmostEqual(position_size, 250.0)

    def test_liquidity_cap_applied(self):
        """Test that position size is capped when liquidity is low."""
        # Base risk $5, stop distance 0.02. Size = 250.
        # Liquidity depth $1000, 10% cap = 100.
        # 250 > 100, so size should be capped at 100.
        # Recalculated risk = 100 * 0.02 = 2.0.
        risk_amount, position_size = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=1.0,
            stop_distance=0.02,
            available_liquidity=1000.0
        )
        self.assertAlmostEqual(position_size, 100.0)
        self.assertAlmostEqual(risk_amount, 2.0)

    def test_custom_liquidity_ratio(self):
        """Test that custom max_liquidity_ratio is respected."""
        # Base risk $5, stop distance 0.02. Size = 250.
        # Liquidity depth $1000, 5% cap = 50.
        risk_amount, position_size = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=1.0,
            stop_distance=0.02,
            available_liquidity=1000.0,
            max_liquidity_ratio=0.05
        )
        self.assertAlmostEqual(position_size, 50.0)
        self.assertAlmostEqual(risk_amount, 1.0)

    def test_zero_liquidity_skips_cap(self):
        """Test that zero or None liquidity skips the capping logic."""
        # If liquidity is 0, the check 'available_liquidity > 0' fails, skipping the cap.
        risk_amount, position_size = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=1.0,
            stop_distance=0.02,
            available_liquidity=0.0
        )
        self.assertAlmostEqual(position_size, 250.0)
        self.assertAlmostEqual(risk_amount, 5.0)

if __name__ == '__main__':
    unittest.main()
