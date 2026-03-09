import unittest
import sys
import os

# Add the root directory to sys.path to import project modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import risk_manager

class TestRiskManagerWith100USDT(unittest.TestCase):

    def setUp(self):
        """Set up a fresh RiskManager instance for each test with a $100 balance."""
        os.environ["RISK_MODEL"] = "dynamic_kelly"
        # For a $100 balance, the adaptive risk falls between min and max
        # Ratio = (100-50)/(500-50) = 0.111...
        # Adaptive Pct = 0.05 - (0.111... * (0.05-0.005)) = 0.045, or 4.5%
        # Base risk amount is $100 * 0.045 = $4.5
        self.risk_manager = risk_manager.RiskManager()
        self.balance = 100.0
        self.base_risk = 4.5

    def test_risk_calculation_full_confidence(self):
        """Test risk amount with 1.0 signal confidence."""
        # For new accounts, Kelly defaults to adaptive pct.
        # Confidence scalar = 0.5 + 0.5 * 1.0 = 1.0
        # Expected risk = $4.5 * 1.0 = $4.5
        risk_amount, _ = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=1.0,
            open_positions=[]
        )
        self.assertAlmostEqual(risk_amount, self.base_risk)

    def test_risk_calculation_half_confidence(self):
        """Test risk amount with 0.5 signal confidence."""
        # Confidence scalar = 0.5 + 0.5 * 0.5 = 0.75
        # Expected risk = $4.5 * 0.75 = $3.375
        risk_amount, _ = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=0.5,
            open_positions=[]
        )
        self.assertAlmostEqual(risk_amount, 3.375)

    def test_risk_calculation_zero_confidence(self):
        """Test risk amount with 0.0 signal confidence."""
        # Confidence scalar = 0.5 + 0.5 * 0.0 = 0.5
        # Expected risk = $4.5 * 0.5 = $2.25
        risk_amount, _ = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=0.0,
            open_positions=[]
        )
        self.assertAlmostEqual(risk_amount, 2.25)
        
    def test_position_sizing(self):
        """Test that position size is calculated correctly from risk amount."""
        # Risk = $4.5, stop distance = $10. Size = 4.5 / 10 = 0.45
        risk_amount, position_size = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=1.0,
            stop_distance=10.0,
            open_positions=[]
        )
        self.assertAlmostEqual(risk_amount, self.base_risk)
        self.assertAlmostEqual(position_size, 0.45)

    def test_portfolio_exposure_rejection(self):
        """Test that trades are rejected when max portfolio risk is exceeded."""
        # Max portfolio risk on $100 balance is 30% = $30.
        # Base risk per trade is $4.5. 6 trades = $27 risk. 7 trades = $31.5 risk.
        
        # 6 open positions should leave capacity for one more trade.
        open_positions = [{"pnl": 0, "margin": self.base_risk}] * 6
        current_risk = self.risk_manager.get_open_position_risk(open_positions)
        self.assertAlmostEqual(current_risk, 27.0)

        # A new trade risking $3 should be allowed.
        rejected, _ = self.risk_manager.should_reject_trade(3.0, self.balance, open_positions)
        self.assertFalse(rejected)
        
        # A new trade risking $3.01 should be rejected ($27 + $3.01 > $30).
        rejected, reason = self.risk_manager.should_reject_trade(3.01, self.balance, open_positions)
        self.assertTrue(rejected)
        self.assertIn("Portfolio risk cap", reason)

    def test_risk_capacity_limit(self):
        """Test that risk amount is capped by remaining portfolio capacity."""
        # 6 open positions, $27 total risk. Capacity remaining is $30 - $27 = $3.
        open_positions = [{"pnl": 0, "margin": self.base_risk}] * 6
        
        # Even with full confidence, the risk amount should be capped at the remaining $3.
        risk_amount, _ = self.risk_manager.compute_dynamic_risk(
            account_balance=self.balance,
            signal_strength=1.0,
            open_positions=open_positions
        )
        self.assertAlmostEqual(risk_amount, 3.0)

if __name__ == '__main__':
    unittest.main()
