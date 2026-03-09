import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import time
from unittest.mock import patch

import sys
import os

# Add the root directory to sys.path to import project modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.drawdown_guard import DrawdownGuard

class TestDrawdownGuardExhaustive(unittest.TestCase):
    def setUp(self):
        """Set up a guard with a $100 balance and 10% drawdown limit for easy testing."""
        self.guard = DrawdownGuard(max_drawdown=0.10)
        self.balance = 100.0
        self.guard.set_start_balance(self.balance)

    def test_no_loss_allows_trade(self):
        """Should allow trades when there is no loss."""
        ok, reason = self.guard.can_open_trade(self.balance)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_small_loss_allows_trade(self):
        """Should allow trades when loss is within the threshold."""
        # Lose $5 (5% loss), which is less than the 10% limit.
        pnl = -5.0
        self.balance += pnl
        self.guard.record_pnl(pnl, self.balance)
        
        ok, reason = self.guard.can_open_trade(self.balance)
        self.assertTrue(ok)

    def test_exact_loss_breach_blocks_trade(self):
        """Should block trades when the loss hits the exact threshold."""
        # Lose $10 (10% loss).
        pnl = -10.0
        self.balance += pnl
        self.guard.record_pnl(pnl, self.balance)

        ok, reason = self.guard.can_open_trade(self.balance)
        self.assertFalse(ok)
        self.assertIn("Daily loss 10.00% ≥ threshold 10.00%", reason)

    def test_loss_over_threshold_blocks_trade(self):
        """Should block trades when the loss exceeds the threshold."""
        # Lose $15 (15% loss).
        pnl = -15.0
        self.balance += pnl
        self.guard.record_pnl(pnl, self.balance)
        
        ok, reason = self.guard.can_open_trade(self.balance)
        self.assertFalse(ok)
        self.assertIn("Daily loss 15.00%", reason)
        
    def test_kill_switch_persists(self):
        """Once the kill switch is active, it should remain active for the day."""
        # Lose $15, activating the switch.
        self.guard.record_pnl(-15.0, 85.0)
        ok, _ = self.guard.can_open_trade(85.0)
        self.assertFalse(ok, "Trade should be blocked after initial breach.")
        
        # A subsequent winning trade should not deactivate the switch.
        self.guard.record_pnl(5.0, 90.0) # PnL is now -10, but switch should persist.
        ok, _ = self.guard.can_open_trade(90.0)
        self.assertFalse(ok, "Kill switch should persist even if PnL recovers slightly.")

    @patch('modules.drawdown_guard.DrawdownGuard._today')
    def test_daily_reset_logic(self, mock_today):
        """Test that the drawdown state and kill switch reset on a new day."""
        # --- Day 1: Get killed ---
        mock_today.return_value = "2026-03-09"
        self.guard.set_start_balance(100.0)
        self.guard.record_pnl(-15.0, 85.0)
        ok, _ = self.guard.can_open_trade(85.0)
        self.assertFalse(ok, "Trade should be blocked on Day 1.")
        
        # --- Day 2: Should reset and allow trades ---
        mock_today.return_value = "2026-03-10"
        
        # The reset happens on the first action of the new day.
        # Let's check can_open_trade. The start_balance will be reset to the current 85.0.
        ok, reason = self.guard.can_open_trade(85.0)
        self.assertTrue(ok, "Kill switch should reset on a new day.")
        self.assertEqual(self.guard._state.start_balance, 85.0, "Start balance should reset.")
        self.assertEqual(self.guard._state.daily_pnl, 0.0, "Daily PnL should reset.")

        # Now, a 10% loss from the *new* start balance should trigger the switch.
        # 10% of 85.0 is 8.5
        self.guard.record_pnl(-8.6, 76.4)
        ok, _ = self.guard.can_open_trade(76.4)
        self.assertFalse(ok, "Kill switch should re-trigger based on the new day's start balance.")

if __name__ == '__main__':
    unittest.main()
