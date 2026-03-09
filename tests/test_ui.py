import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import core.ui as ui
from colorama import Fore, Style

class TestUI(unittest.TestCase):
    def test_hr_functions(self):
        self.assertIsInstance(ui.hr_double(), str)
        self.assertIsInstance(ui.hr_thin(), str)
        self.assertIsInstance(ui.hr_dash(), str)
        self.assertIsInstance(ui.hr_heavy(), str)

    def test_score_gauge(self):
        self.assertIsInstance(ui.score_gauge(100), str)
        self.assertIsInstance(ui.score_gauge(0), str)
        self.assertIsInstance(ui.score_gauge(200), str)
        self.assertIsInstance(ui.score_gauge(-10), str)
        self.assertTrue(len(ui.score_gauge(100)) > 0)

    def test_sparkline(self):
        vals = [10, 20, 30, 40, 50, 40, 30, 20, 10]
        spark = ui.sparkline(vals)
        self.assertIsInstance(spark, str)
        self.assertTrue(len(spark) > 0)
        self.assertEqual(ui.sparkline([]), "─" * 16)

    def test_grade_badge(self):
        # Depending on if phemex_common is importable, this might return "" or a badge
        badge = ui.grade_badge(80)
        self.assertIsInstance(badge, str)

    def test_section_headers(self):
        self.assertIsInstance(ui.section("Test"), str)
        self.assertIsInstance(ui.section_left("Test"), str)

    def test_colored_values(self):
        self.assertIn(Fore.LIGHTGREEN_EX, ui.pnl_color(10.0))
        self.assertIn(Fore.RED, ui.pnl_color(-10.0))
        
        colored_val = ui.colored(123.456, fmt=".2f")
        self.assertIn("123.46", colored_val)

    def test_dir_label(self):
        self.assertIn("LONG", ui.dir_label("LONG"))
        self.assertIn("SHORT", ui.dir_label("SHORT"))

    def test_wr_bar(self):
        self.assertIsInstance(ui.wr_bar(60.0), str)
        self.assertIsInstance(ui.wr_bar(40.0), str)

    def test_box_drawing(self):
        self.assertIsInstance(ui.box_top(), str)
        self.assertIsInstance(ui.box_mid(), str)
        self.assertIsInstance(ui.box_bot(), str)
        self.assertIsInstance(ui.box_row("Text"), str)

if __name__ == '__main__':
    unittest.main()
