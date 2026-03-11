#!/usr/bin/env python3
"""
filter_output.py — pipe backtest.py output through this to show only:
  • The MISSION REPORT box (💠━━━ ... ┗━━━)
  • LONG / SHORT summary lines
  • Exit type lines (trail_stop, hard_stop, take_profit, max_hold)

PnL values are colored green (positive) or red (negative).

Usage:
  python backtest.py [args] | python filter_output.py
"""
import sys
import re

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"

EXIT_KEYS      = ("trail_stop", "hard_stop", "take_profit", "max_hold")
DIRECTION_KEYS = ("LONG  :", "SHORT :")

def colorize(line):
    def replacer(m):
        val = m.group(0)
        color = GREEN if val.startswith("+") else RED
        return f"{color}{val}{RESET}"
    return re.sub(r"[+-]\d+\.\d+", replacer, line)

in_box = False

for line in sys.stdin:
    stripped = line.rstrip()

    if "MISSION REPORT" in stripped and "💠" in stripped:
        in_box = True

    if in_box:
        print(colorize(stripped))
        if stripped.startswith("┗"):
            in_box = False
        continue

    if any(stripped.lstrip().startswith(k) for k in DIRECTION_KEYS):
        print(colorize(stripped))
        continue

    if any(stripped.lstrip().startswith(k) for k in EXIT_KEYS):
        print(colorize(stripped))
        continue
