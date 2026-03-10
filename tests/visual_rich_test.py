#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from rich.console import Console
from core.ui_rich import get_account_summary, get_position_row, FB_THEME

# Use the theme and test with standard styles first to ensure it works
console = Console(theme=FB_THEME)

def test_rich_ui():
    console.print("\n--- FANCYBOT RICH UI PROTOTYPE ---\n", style="bold cyan")
    
    # 1. Account Summary
    summary = get_account_summary(
        balance=1245.50,
        upnl=42.30,
        locked_margin=150.0,
        entropy_penalty=0.15,
        initial_balance=1000.0
    )
    console.print(summary)
    
    # 2. Position Rows
    pos1 = get_position_row(
        symbol="BTCUSDT",
        side="Buy",
        entry=65432.1,
        current=66123.4,
        size=0.01,
        margin=50.0,
        pnl=6.91,
        stop_price=64000.0
    )
    console.print(pos1)
    
    pos2 = get_position_row(
        symbol="ETHUSDT",
        side="Sell",
        entry=3512.4,
        current=3550.2,
        size=0.5,
        margin=100.0,
        pnl=-18.90,
        stop_price=3600.0
    )
    console.print(pos2)

if __name__ == "__main__":
    test_rich_ui()
