import unittest
from drawdown_guard import set_start_balance, record_pnl, can_open_trade

class TestDrawdownGuard(unittest.TestCase):
    def test_drawdown_logic(self):
        set_start_balance(1000.0)

        # Small loss, should be OK
        record_pnl(-20.0, 980.0)
        ok, reason = can_open_trade(980.0)
        self.assertTrue(ok)

        # Total loss 6%, should block (default 5%)
        record_pnl(-40.0, 940.0)
        ok, reason = can_open_trade(940.0)
        self.assertFalse(ok)
        self.assertIn("daily loss", reason.lower())

if __name__ == '__main__':
    unittest.main()
