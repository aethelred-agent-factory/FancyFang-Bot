import random

# ── Parameter grids ──────────────────────────────────────────────
# Formats verified against backtest.py argparse + backtest.sh real examples:
#
#   min_score     → type=float, default=25.0
#                   backtest.sh uses 3–8 for fast scalp TFs, 40–72 for slower TFs
#   min_score_gap → type=float, default=0.0
#                   backtest.sh uses whole numbers: 1, 2, 3, 4, 5, 8, 10, 12, 15
#   trail_pct     → type=float, already a decimal ratio (0.02 = 2%)
#                   backtest.sh uses 0.01 – 0.08
#   stop_loss_pct → type=float, already a decimal ratio (0.03 = 3%)
#   take_profit   → type=float, already a decimal ratio (0.05 = 5%)
#   timeframes    → matched exactly to backtest.sh casing
#
PARAMS = {
    "timeframe": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"],
    "candles": [500, 1000],
    "min_score": [3, 4, 5, 6, 7, 8, 25, 40, 45, 50, 55, 58, 60, 62, 65, 68, 70, 72],
    "min_signals": [2, 3, 4, 5, 6],
    "trail_pct": [
        0.0,
        0.01,
        0.012,
        0.015,
        0.018,
        0.02,
        0.025,
        0.03,
        0.035,
        0.04,
        0.05,
        0.06,
        0.07,
        0.08,
    ],
    "leverage": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15],
    "margin": [
        2.0,
        3.0,
        4.0,
        5.0,
        6.0,
        8.0,
        10.0,
        12.0,
        15.0,
        18.0,
        20.0,
        25.0,
        30.0,
        40.0,
        50.0,
    ],
    "max_margin": [
        50.0,
        80.0,
        100.0,
        120.0,
        130.0,
        140.0,
        150.0,
        180.0,
        200.0,
        220.0,
        250.0,
        300.0,
        400.0,
        500.0,
        600.0,
    ],
    "max_hold": [
        5,
        8,
        10,
        12,
        15,
        18,
        20,
        25,
        30,
        35,
        40,
        45,
        48,
        50,
        56,
        60,
        70,
        72,
        80,
        84,
        90,
        96,
        120,
        160,
        180,
        200,
    ],
    "min_vol": [
        0,
        5_000_000,
        8_000_000,
        10_000_000,
        12_000_000,
        15_000_000,
        20_000_000,
    ],
    "stop_loss_pct": [
        0.0,
        0.01,
        0.012,
        0.015,
        0.018,
        0.02,
        0.022,
        0.025,
        0.028,
        0.03,
        0.04,
        0.045,
        0.05,
        0.06,
        0.07,
        0.08,
        0.09,
        0.1,
    ],
    "take_profit_pct": [
        0.0,
        0.02,
        0.025,
        0.03,
        0.036,
        0.04,
        0.045,
        0.048,
        0.05,
        0.055,
        0.06,
        0.065,
        0.07,
        0.08,
        0.09,
        0.1,
        0.11,
        0.12,
        0.14,
        0.15,
        0.16,
        0.18,
        0.2,
        0.22,
    ],
    "cooldown": [0, 1, 2, 3, 4, 5, 6, 8],
    "direction": ["LONG", "SHORT", "BOTH"],
    "min_score_gap": [0, 1, 2, 3, 4, 5, 8, 10, 12, 15],
    "window": [50, 100, 150, 200],
    "no_htf": [True, False],
}

TARGET = 100_000
OUTPUT = "/mnt/user-data/outputs/backtest_commands.txt"


def build_command(combo):
    parts = ["python backtest.py"]
    parts.append(f"--timeframe {combo['timeframe']}")
    parts.append(f"--candles {combo['candles']}")
    parts.append(f"--min-score {combo['min_score']}")
    parts.append(f"--min-signals {combo['min_signals']}")
    if combo["trail_pct"] > 0:
        parts.append(f"--trail-pct {combo['trail_pct']}")
    parts.append(f"--leverage {combo['leverage']}")
    parts.append(f"--margin {combo['margin']}")
    parts.append(f"--max-margin {combo['max_margin']}")
    parts.append(f"--max-hold {combo['max_hold']}")
    if combo["min_vol"] > 0:
        parts.append(f"--min-vol {combo['min_vol']}")
    parts.append(f"--stop-loss-pct {combo['stop_loss_pct']}")
    parts.append(f"--take-profit-pct {combo['take_profit_pct']}")
    parts.append(f"--cooldown {combo['cooldown']}")
    parts.append(f"--direction {combo['direction']}")
    if combo["min_score_gap"] > 0:
        parts.append(f"--min-score-gap {combo['min_score_gap']}")
    parts.append(f"--window {combo['window']}")
    if combo["no_htf"]:
        parts.append("--no-htf")
    parts.append("--csv")
    return " ".join(parts)


def main():
    keys = list(PARAMS.keys())
    values = list(PARAMS.values())

    total_possible = 1
    for v in values:
        total_possible *= len(v)
    print(f"Total possible combinations: {total_possible:,}")
    print(f"Generating {TARGET:,} unique random samples...\n")

    seen = set()
    results = []

    attempts = 0
    while len(results) < TARGET:
        attempts += 1
        combo = {k: random.choice(v) for k, v in PARAMS.items()}
        key = tuple(combo[k] for k in keys)
        if key in seen:
            continue
        seen.add(key)
        cmd = build_command(combo)
        results.append(cmd)

        if len(results) % 10_000 == 0:
            print(f"  Generated {len(results):,} commands...")

    print(
        f"\nDone! {len(results):,} unique commands generated ({attempts:,} attempts)."
    )

    with open(OUTPUT, "w") as f:
        f.write("# FancyFangBot Backtest Command Universe\n")
        f.write(f"# Total unique commands: {len(results):,}\n")
        f.write(f"# Total possible space:  {total_possible:,}\n")
        f.write("# Copy-paste any line below to run a backtest\n")
        f.write("#\n")
        f.write("# Parameter formats (verified against backtest.py):\n")
        f.write("#   --min-score       integer-like float (3-72 range)\n")
        f.write("#   --min-score-gap   whole number float (0-15)\n")
        f.write("#   --trail-pct       decimal ratio — 0.02 = 2%  (omitted if 0)\n")
        f.write("#   --stop-loss-pct   decimal ratio — 0.03 = 3%\n")
        f.write("#   --take-profit-pct decimal ratio — 0.05 = 5%\n")
        f.write("#" + "-" * 80 + "\n\n")
        for cmd in results:
            f.write(f"{cmd}\n")

    print(f"Saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
