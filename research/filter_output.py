#!/usr/bin/env python3
"""
filter_output.py — pipe backtest.py output through this to show only:
  • The MISSION REPORT box (💠━━━ ... ┗━━━)
  • LONG / SHORT summary lines
  • Exit type lines (trail_stop, hard_stop, take_profit, max_hold)

Usage:
  python backtest.py [args] | python filter_output.py
"""
import sys

EXIT_KEYS = ("trail_stop", "hard_stop", "take_profit", "max_hold")
DIRECTION_KEYS = ("LONG  :", "SHORT :")

in_box = False

for line in sys.stdin:
    stripped = line.rstrip()

    # Start of MISSION REPORT box
    if "MISSION REPORT" in stripped and "💠" in stripped:
        in_box = True

    # Print everything inside the box
    if in_box:
        print(stripped)
        # End of box
        if stripped.startswith("┗"):
            in_box = False
        continue

    # LONG / SHORT direction summary lines (right after the box)
    if any(stripped.lstrip().startswith(k) for k in DIRECTION_KEYS):
        print(stripped)
        continue

    # Exit type breakdown lines
    if any(stripped.lstrip().startswith(k) for k in EXIT_KEYS):
        print(stripped)
        continue
