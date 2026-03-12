import json
import os
import random
import subprocess
import time
import uuid

TIMEFRAME = "5m"
CANDLES = 1000
ITERATIONS = 100000

# parameter ranges
RANGES = {
    "min-score": (0.5, 5.0),
    "min-signals": (1, 6),
    "trail-pct": (0.002, 0.03),
    "leverage": (1, 20),
    "margin": (2, 20),
    "max-margin": (20, 200),
    "max-hold": (12, 120),
    "min-vol": (0, 3000000),
    "stop-loss-pct": (0.002, 0.05),
    "take-profit-pct": (0.01, 0.15),
    "cooldown": (0, 50),
    "min-score-gap": (0.0, 2.0),
    "window": (50, 200),
}

DIRECTIONS = ["LONG", "SHORT", "BOTH"]

best_score = -1e18
best_params = None

start_time = time.time()


# ---------------------------------
# random helper
# ---------------------------------


def rand(a, b):
    if isinstance(a, int) and isinstance(b, int):
        return random.randint(a, b)
    return round(random.uniform(a, b), 6)


# ---------------------------------
# generate parameter set
# ---------------------------------


def generate_params():

    params = {}

    for k, (a, b) in RANGES.items():
        params[k] = rand(a, b)

    params["direction"] = random.choice(DIRECTIONS)

    return params


# ---------------------------------
# run backtest
# ---------------------------------


def run_backtest(params):

    outfile = f"fuzz_{uuid.uuid4().hex}.json"

    cmd = [
        "python",
        "backtest.py",
        "--timeframe",
        TIMEFRAME,
        "--candles",
        str(CANDLES),
        "--output",
        outfile,
    ]

    for k, v in params.items():
        cmd.append(f"--{k}")
        cmd.append(str(v))

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not os.path.exists(outfile):
        return None

    data = json.load(open(outfile))
    os.remove(outfile)

    # -------------------------------
    # Handle LIST output (trade log)
    # -------------------------------

    if isinstance(data, list):

        total_pnl = 0
        wins = 0
        trades = 0

        for trade in data:

            pnl = trade.get("pnl", 0)

            total_pnl += pnl

            if pnl > 0:
                wins += 1

            trades += 1

        winrate = wins / trades if trades else 0

    # -------------------------------
    # Handle DICT output (summary)
    # -------------------------------

    else:

        total_pnl = data.get("total_pnl", 0)
        winrate = data.get("winrate", 0)
        trades = data.get("trades", 0)

    score = total_pnl * (0.5 + winrate)

    return total_pnl, winrate, trades, score


# ---------------------------------
# main fuzz loop
# ---------------------------------

for i in range(ITERATIONS):

    params = generate_params()

    result = run_backtest(params)

    if not result:
        continue

    pnl, winrate, trades, score = result

    elapsed = time.time() - start_time
    speed = i / elapsed if elapsed > 0 else 0

    print(
        f"\nITERATION {i}"
        f"\nPnL: {pnl:.2f}"
        f"\nWinrate: {winrate:.2f}"
        f"\nTrades: {trades}"
        f"\nScore: {score:.2f}"
        f"\nParams: {params}"
        f"\nSpeed: {speed:.2f} tests/sec"
    )

    if score > best_score:

        best_score = score
        best_params = params

        print("\n🔥 NEW BEST STRATEGY FOUND 🔥")
        print("Best Score:", best_score)
        print("Best Params:", best_params)
        print("")
