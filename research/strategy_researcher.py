
import subprocess
import os
import json
import re
import argparse
import core.phemex_common as pc

# REF: [Tier 2] Consolidated Research Tool
# This script consolidates the logic for running targeted backtests on top symbols.

DEFAULT_SYMBOLS = "BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT SUIUSDT LINKUSDT NAS100USDT DOGEUSDT TAOUSDT XLMUSDT PIXELUSDT ADAUSDT AVAXUSDT LTCUSDT u1000SHIBUSDT OPUSDT BCHUSDT ENAUSDT NEARUSDT"

def run_backtest(cmd_base, symbols):
    # Security: Use argument list to avoid shell=True where possible
    # But cmd_base is a full command string from the universe, so we split it carefully
    cmd_parts = cmd_base.split()
    cmd_parts.extend(["--symbols", symbols, "--output", "research/temp_bt.json"])

    print(f"Running: {' '.join(cmd_parts)}")
    try:
        # REF: [Tier 1] Using subprocess with arg list
        result = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=600)
        stdout = result.stdout

        stats = {}
        pnl_match = re.search(r"Total PnL\s+:\s+([+-]?\d+\.\d+)", stdout)
        if pnl_match:
            stats['pnl'] = float(pnl_match.group(1))

        wr_match = re.search(r"Win Rate\s+:\s+(\d+\.\d+)%", stdout)
        if wr_match:
            stats['win_rate'] = float(wr_match.group(1))

        exp_match = re.search(r"Expectancy\s+:\s+([+-]?\d+\.\d+)", stdout)
        if exp_match:
            stats['expectancy'] = float(exp_match.group(1))

        trades_match = re.search(r"Trades\s+:\s+(\d+)", stdout)
        if trades_match:
            stats['trades'] = int(trades_match.group(1))

        return stats
    except Exception as e:
        print(f"Error running backtest: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="FancyBot Research Runner")
    parser.add_argument("--mode", choices=["targeted", "verify"], default="targeted")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    args = parser.parse_args()

    if args.mode == "targeted":
        candidates = [
            "python research/backtest.py --timeframe 30m --candles 1000 --min-score 65 --min-signals 6 --trail-pct 0.015 --leverage 10 --margin 10.0 --max-margin 100.0 --max-hold 24 --min-vol 10000000 --stop-loss-pct 0.03 --take-profit-pct 0.05 --cooldown 1 --direction BOTH --min-score-gap 15 --window 50 --csv",
            "python research/backtest.py --timeframe 4h --candles 1000 --min-score 50 --min-signals 6 --trail-pct 0.05 --leverage 10 --margin 20.0 --max-margin 200.0 --max-hold 96 --min-vol 10000000 --stop-loss-pct 0.05 --take-profit-pct 0.1 --cooldown 5 --direction SHORT --min-score-gap 5 --window 150 --csv"
        ]

        results = []
        for cmd in candidates:
            stats = run_backtest(cmd, args.symbols)
            if stats:
                results.append({"command": cmd, "stats": stats})

        print("\n--- Research Results ---")
        print(json.dumps(results, indent=2))

    elif args.mode == "verify":
        winner = "python research/backtest.py --timeframe 30m --candles 1000 --min-score 65 --min-signals 6 --trail-pct 0.015 --leverage 10 --margin 10.0 --max-margin 100.0 --max-hold 24 --min-vol 10000000 --stop-loss-pct 0.03 --take-profit-pct 0.05 --cooldown 1 --direction BOTH --min-score-gap 15 --window 50 --csv"
        stats = run_backtest(winner, args.symbols)
        print("\n--- Final Winner Verification ---")
        print(json.dumps(stats, indent=2))

if __name__ == "__main__":
    main()
