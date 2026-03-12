#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.ui import braille_progress_bar, cyber_telemetry, glow_panel


def main():
    print("\n" + "=" * 80)
    print(" FANCYBOT VISUAL TEST SUITE")
    print("=" * 80 + "\n")

    # Test Braille Progress Bar
    print("1. Braille Progress Bars (0% to 100%)")
    for i in range(0, 101, 10):
        print(f"{i:>3}%: {braille_progress_bar(i, width=40)}")
    print("\n")

    # Test Cyber Telemetry
    print("2. Cyber Telemetry Indicators")
    print(cyber_telemetry("Balance", 1250.45, 2000.0, "$"))
    print(cyber_telemetry("PnL", 45.12, 100.0, "$"))
    print(cyber_telemetry("Loss", -12.50, 100.0, "$"))
    print(cyber_telemetry("CPU", 65.5, 100.0, "%"))
    print("\n")

    # Test Glow Panel
    print("3. Glow Panel (Cyan)")
    print(
        glow_panel(
            "SYSTEM STATUS",
            [
                "Core Engine: ONLINE",
                "Sensors: CALIBRATED",
                "Targeting: ACTIVE",
                cyber_telemetry("Uptime", 98.4, 100.0, "%"),
            ],
            color_rgb=(0, 255, 255),
            width=60,
        )
    )

    print("\n4. Glow Panel (Magenta)")
    print(
        glow_panel(
            "THREAT DETECTED",
            ["Asset: SOLUSDT", "Risk Level: HIGH", "Action: MONITORING"],
            color_rgb=(255, 0, 255),
            width=60,
        )
    )

    print("\n" + "=" * 80)
    print(" TEST SUITE COMPLETE")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
