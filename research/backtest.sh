 #!/bin/bash

# List of backtest commands (each as a single string)
commands=(
    "python backtest.py --timeframe 1m --candles 1000 --min-score 4 --min-signals 2 --leverage 12 --margin 3 --max-margin 80 --stop-loss-pct 0.012 --take-profit-pct 0.025 --trail-pct 0.015 --max-hold 15 --direction BOTH --cooldown 0 --no-htf --csv --output scalp_1m_01.json"
    "python backtest.py --timeframe 1m --candles 500 --min-score 5 --min-signals 3 --leverage 8 --margin 5 --max-margin 100 --stop-loss-pct 0.02 --take-profit-pct 0.04 --trail-pct 0.02 --max-hold 10 --direction BOTH --cooldown 1 --csv --output scalp_1m_02.json"
    "python backtest.py --timeframe 1m --candles 1000 --min-score 6 --min-signals 3 --leverage 10 --margin 4 --max-margin 120 --stop-loss-pct 0.015 --take-profit-pct 0.03 --max-hold 8 --direction BOTH --cooldown 0 --min-score-gap 1 --csv --output scalp_1m_gap.json"
    "python backtest.py --timeframe 1m --candles 500 --min-score 3 --min-signals 2 --leverage 15 --margin 2 --max-margin 50 --stop-loss-pct 0.01 --take-profit-pct 0.02 --trail-pct 0.01 --max-hold 5 --direction LONG --cooldown 0 --csv --output scalp_1m_long_only.json"
    "python backtest.py --timeframe 3m --candles 1000 --min-score 55 --min-signals 3 --leverage 7 --margin 8 --max-margin 150 --stop-loss-pct 0.025 --take-profit-pct 0.05 --trail-pct 0.03 --max-hold 30 --direction BOTH --cooldown 2 --min-vol 10000000 --csv --output quick_3m_vol.json"
    "python backtest.py --timeframe 3m --candles 500 --min-score 58 --min-signals 4 --leverage 5 --margin 10 --max-margin 200 --stop-loss-pct 0.03 --take-profit-pct 0.07 --trail-pct 0.025 --max-hold 45 --direction SHORT --cooldown 3 --csv --output quick_3m_short.json"
    "python backtest.py --timeframe 3m --candles 1000 --min-score 50 --min-signals 2 --leverage 9 --margin 6 --max-margin 120 --stop-loss-pct 0.02 --take-profit-pct 0 --trail-pct 0.04 --max-hold 25 --direction BOTH --cooldown 1 --csv --output quick_3m_trail_only.json"
    "python backtest.py --timeframe 3m --candles 500 --min-score 62 --min-signals 4 --leverage 6 --margin 8 --max-margin 140 --stop-loss-pct 0.028 --take-profit-pct 0.06 --max-hold 35 --direction BOTH --min-score-gap 2 --cooldown 2 --csv --output quick_3m_gap2.json"
    "python backtest.py --timeframe 5m --candles 1000 --min-score 45 --min-signals 3 --leverage 8 --margin 6 --max-margin 120 --stop-loss-pct 0.018 --take-profit-pct 0.036 --trail-pct 0.012 --max-hold 18 --direction BOTH --cooldown 2 --min-vol 5000000 --csv --output 5m_mid_vol.json"
    "python backtest.py --timeframe 5m --candles 500 --min-score 68 --min-signals 4 --leverage 4 --margin 12 --max-margin 180 --stop-loss-pct 0.022 --take-profit-pct 0.055 --trail-pct 0.02 --max-hold 40 --direction LONG --min-score-gap 3 --cooldown 4 --csv --output 5m_long_conservative.json"
    "python backtest.py --timeframe 5m --candles 1000 --min-score 5 --min-signals 4 --leverage 6 --margin 8 --max-margin 150 --stop-loss-pct 0.025 --take-profit-pct 0.06 --max-hold 30 --direction BOTH --cooldown 2 --no-htf --csv --output 5m_no_htf.json"
    "python backtest.py --timeframe 5m --candles 500 --min-score 72 --min-signals 3 --leverage 5 --margin 10 --max-margin 200 --stop-loss-pct 0.02 --take-profit-pct 0.05 --trail-pct 0.015 --max-hold 60 --direction SHORT --cooldown 5 --csv --output 5m_short_slower.json"
    "python backtest.py --timeframe 5m --candles 1000 --min-score 3 --min-signals 3 --leverage 12 --margin 4 --max-margin 100 --stop-loss-pct 0.015 --take-profit-pct 0.03 --max-hold 12 --direction BOTH --cooldown 0 --csv --output 5m_super_scalp.json"
    "python backtest.py --timeframe 15m --candles 1000 --min-score 6 --min-signals 5 --leverage 4 --margin 15 --max-margin 200 --stop-loss-pct 0.04 --take-profit-pct 0.09 --trail-pct 0.03 --max-hold 48 --direction BOTH --min-score-gap 2 --cooldown 3 --csv --output 15m_balanced_01.json"
    "python backtest.py --timeframe 15m --candles 500 --min-score 5 --min-signals 4 --leverage 5 --margin 12 --max-margin 180 --stop-loss-pct 0.035 --take-profit-pct 0.08 --trail-pct 0.025 --max-hold 56 --direction LONG --cooldown 2 --csv --output 15m_long_swing.json"
    "python backtest.py --timeframe 15m --candles 1000 --min-score 7 --min-signals 5 --leverage 3 --margin 20 --max-margin 250 --stop-loss-pct 0.05 --take-profit-pct 0.1 --trail-pct 0.04 --max-hold 72 --direction BOTH --min-score-gap 3 --cooldown 4 --csv --output 15m_high_score.json"
    "python backtest.py --timeframe 15m --candles 500 --min-score 4 --min-signals 3 --leverage 8 --margin 8 --max-margin 120 --stop-loss-pct 0.025 --take-profit-pct 0.06 --max-hold 30 --direction SHORT --cooldown 1 --csv --output 15m_short_aggressive.json"
    "python backtest.py --timeframe 15m --candles 1000 --min-score 5 --min-signals 4 --leverage 6 --margin 10 --max-margin 150 --stop-loss-pct 0.03 --take-profit-pct 0 --trail-pct 0.035 --max-hold 80 --direction BOTH --cooldown 2 --csv --output 15m_trail_focus.json"
    "python backtest.py --timeframe 30m --candles 1000 --min-score 5 --min-signals 4 --leverage 5 --margin 15 --max-margin 200 --stop-loss-pct 0.045 --take-profit-pct 0.1 --trail-pct 0.03 --max-hold 60 --direction BOTH --min-score-gap 1 --cooldown 3 --csv --output 30m_mid.json"
    "python backtest.py --timeframe 30m --candles 500 --min-score 6 --min-signals 5 --leverage 4 --margin 18 --max-margin 220 --stop-loss-pct 0.05 --take-profit-pct 0.12 --trail-pct 0.04 --max-hold 80 --direction LONG --cooldown 4 --min-vol 8000000 --csv --output 30m_long_vol.json"
    "python backtest.py --timeframe 30m --candles 1000 --min-score 4 --min-signals 3 --leverage 7 --margin 10 --max-margin 150 --stop-loss-pct 0.03 --take-profit-pct 0.07 --max-hold 45 --direction BOTH --cooldown 2 --csv --output 30m_fast_swing.json"
    "python backtest.py --timeframe 1h --candles 1000 --min-score 6 --min-signals 5 --leverage 3 --margin 20 --max-margin 300 --stop-loss-pct 0.06 --take-profit-pct 0.14 --trail-pct 0.05 --max-hold 96 --direction BOTH --min-score-gap 2 --cooldown 4 --csv --output 1h_swing_01.json"
    "python backtest.py --timeframe 1h --candles 500 --min-score 7 --min-signals 6 --leverage 2 --margin 30 --max-margin 400 --stop-loss-pct 0.07 --take-profit-pct 0.16 --trail-pct 0.06 --max-hold 120 --direction LONG --min-score-gap 3 --cooldown 6 --csv --output 1h_long_heavy.json"
    "python backtest.py --timeframe 1h --candles 1000 --min-score 5 --min-signals 4 --leverage 4 --margin 15 --max-margin 250 --stop-loss-pct 0.04 --take-profit-pct 0.09 --max-hold 72 --direction SHORT --cooldown 3 --csv --output 1h_short_moderate.json"
    "python backtest.py --timeframe 1h --candles 500 --min-score 5 --min-signals 5 --leverage 5 --margin 12 --max-margin 180 --stop-loss-pct 0.05 --take-profit-pct 0.11 --trail-pct 0.04 --max-hold 84 --direction BOTH --cooldown 5 --min-vol 10000000 --csv --output 1h_vol_filter.json"
    "python backtest.py --timeframe 2h --candles 1000 --min-score 6 --min-signals 5 --leverage 3 --margin 25 --max-margin 300 --stop-loss-pct 0.06 --take-profit-pct 0.15 --trail-pct 0.05 --max-hold 120 --direction BOTH --min-score-gap 2 --cooldown 4 --csv --output 2h_swing.json"
    "python backtest.py --timeframe 2h --candles 500 --min-score 7 --min-signals 6 --leverage 2 --margin 40 --max-margin 500 --stop-loss-pct 0.08 --take-profit-pct 0.2 --trail-pct 0.07 --max-hold 160 --direction LONG --cooldown 6 --csv --output 2h_long_position.json"
    "python backtest.py --timeframe 4h --candles 1000 --min-score 7 --min-signals 6 --leverage 2 --margin 30 --max-margin 400 --stop-loss-pct 0.08 --take-profit-pct 0.18 --trail-pct 0.06 --max-hold 120 --direction BOTH --min-score-gap 2 --cooldown 5 --csv --output 4h_macro_01.json"
    "python backtest.py --timeframe 4h --candles 500 --min-score 8 --min-signals 7 --leverage 1 --margin 50 --max-margin 600 --stop-loss-pct 0.1 --take-profit-pct 0.22 --trail-pct 0.08 --max-hold 200 --direction LONG --cooldown 8 --min-vol 20000000 --csv --output 4h_long_ultra.json"
    "python backtest.py --timeframe 4h --candles 1000 --min-score 6 --min-signals 5 --leverage 3 --margin 20 --max-margin 300 --stop-loss-pct 0.07 --take-profit-pct 0.16 --max-hold 96 --direction BOTH --cooldown 4 --csv --output 4h_no_trail.json"
    "python backtest.py --symbols BTCUSDT ETHUSDT --timeframe 5m --candles 1000 --min-score 5 --min-signals 4 --leverage 7 --margin 8 --max-margin 150 --stop-loss-pct 0.02 --take-profit-pct 0.045 --trail-pct 0.02 --max-hold 30 --direction BOTH --cooldown 2 --csv --output multi_5m_01.json"
    "python backtest.py --symbols SOLUSDT BNBUSDT ADAUSDT --timeframe 15m --candles 500 --min-score 6 --min-signals 5 --leverage 4 --margin 12 --max-margin 200 --stop-loss-pct 0.035 --take-profit-pct 0.08 --trail-pct 0.03 --max-hold 50 --direction LONG --cooldown 3 --csv --output multi_15m_alt_long.json"
    "python backtest.py --symbols BTCUSDT ETHUSDT SOLUSDT --timeframe 1h --candles 1000 --min-score 6 --min-signals 5 --leverage 3 --margin 20 --max-margin 250 --stop-loss-pct 0.05 --take-profit-pct 0.12 --trail-pct 0.04 --max-hold 72 --direction BOTH --min-score-gap 2 --cooldown 4 --csv --output multi_1h_major.json"
    "python backtest.py --timeframe 5m --candles 1000 --min-score 50 --min-signals 3 --leverage 5 --margin 10 --max-margin 120 --stop-loss-pct 0.018 --take-profit-pct 0.04 --trail-pct 0.02 --max-hold 20 --direction BOTH --min-score-gap 8 --cooldown 2 --csv --output gap_5m_08.json"
    "python backtest.py --timeframe 15m --candles 500 --min-score 45 --min-signals 4 --leverage 6 --margin 8 --max-margin 140 --stop-loss-pct 0.025 --take-profit-pct 0.06 --max-hold 40 --direction SHORT --min-score-gap 12 --cooldown 3 --csv --output gap_15m_12_short.json"
    "python backtest.py --timeframe 1h --candles 1000 --min-score 55 --min-signals 4 --leverage 4 --margin 15 --max-margin 200 --stop-loss-pct 0.04 --take-profit-pct 0.09 --trail-pct 0.03 --max-hold 60 --direction BOTH --min-score-gap 15 --cooldown 4 --min-vol 12000000 --csv --output gap_1h_15_vol.json"
    "python backtest.py --timeframe 3m --candles 1000 --min-score 4 --min-signals 3 --leverage 8 --margin 6 --max-margin 130 --stop-loss-pct 0.022 --take-profit-pct 0.048 --trail-pct 0.018 --max-hold 25 --direction BOTH --cooldown 1 --no-htf --csv --output nohtf_3m_01.json"
    "python backtest.py --timeframe 5m --candles 500 --min-score 5 --min-signals 4 --leverage 7 --margin 8 --max-margin 150 --stop-loss-pct 0.028 --take-profit-pct 0.06 --max-hold 35 --direction LONG --cooldown 2 --no-htf --csv --output nohtf_5m_long.json"
    "python backtest.py --timeframe 15m --candles 1000 --min-score 4 --min-signals 4 --leverage 6 --margin 10 --max-margin 150 --stop-loss-pct 0.025 --take-profit-pct 0 --trail-pct 0.04 --max-hold 90 --direction BOTH --cooldown 2 --csv --output trail_15m_wide.json"
    "python backtest.py --timeframe 5m --candles 500 --min-score 6 --min-signals 3 --leverage 5 --margin 10 --max-margin 120 --stop-loss-pct 0.015 --take-profit-pct 0 --trail-pct 0.025 --max-hold 25 --direction SHORT --cooldown 1 --csv --output trail_5m_short.json"
    "python backtest.py --timeframe 1h --candles 1000 --min-score 7 --min-signals 6 --leverage 2 --margin 25 --max-margin 300 --stop-loss-pct 0.06 --take-profit-pct 0.15 --trail-pct 0.05 --max-hold 120 --direction LONG --min-score-gap 3 --cooldown 5 --csv --output conservative_1h.json"
    "python backtest.py --timeframe 4h --candles 500 --min-score 8 --min-signals 7 --leverage 1 --margin 40 --max-margin 500 --stop-loss-pct 0.09 --take-profit-pct 0.2 --trail-pct 0.07 --max-hold 180 --direction BOTH --cooldown 8 --csv --output ultra_conservative_4h.json"
    "python backtest.py --timeframe 15m --candles 1000 --min-score 5 --min-signals 4 --leverage 5 --margin 12 --max-margin 180 --stop-loss-pct 0.03 --take-profit-pct 0.07 --trail-pct 0.02 --max-hold 48 --direction BOTH --cooldown 2 --min-vol 20000000 --csv --output highvol_15m.json"
    "python backtest.py --timeframe 5m --candles 500 --min-score 4 --min-signals 3 --leverage 8 --margin 6 --max-margin 120 --stop-loss-pct 0.02 --take-profit-pct 0.04 --max-hold 20 --direction LONG --cooldown 1 --min-vol 15000000 --csv --output highvol_5m_long.json"
    "python backtest.py --timeframe 3m --candles 1000 --min-score 40 --min-signals 2 --leverage 10 --margin 5 --max-margin 100 --stop-loss-pct 0.015 --take-profit-pct 0.03 --trail-pct 0.01 --max-hold 12 --direction BOTH --cooldown 0 --csv --output lowscore_3m.json"
    "python backtest.py --timeframe 30m --candles 500 --min-score 65 --min-signals 5 --leverage 3 --margin 20 --max-margin 250 --stop-loss-pct 0.05 --take-profit-pct 0.12 --trail-pct 0.035 --max-hold 70 --direction BOTH --cooldown 4 --csv --output highscore_30m.json"
    "python backtest.py --timeframe 1h --candles 1000 --min-score 5 --min-signals 4 --leverage 4 --margin 15 --max-margin 200 --stop-loss-pct 0.045 --take-profit-pct 0.1 --trail-pct 0.03 --max-hold 60 --direction BOTH --min-score-gap 4 --cooldown 3 --csv --output both_gap4_1h.json"
    "python backtest.py --timeframe 15m --candles 500 --min-score 5 --min-signals 4 --leverage 6 --margin 10 --max-margin 150 --stop-loss-pct 0.03 --take-profit-pct 0.065 --max-hold 45 --direction BOTH --min-score-gap 5 --cooldown 2 --csv --output both_gap5_15m.json"
)

# Loop through each command
for cmd in "${commands[@]}"; do
    echo "======================================================================"
    echo "Running: $cmd"
    echo "======================================================================"

    # Run the command, capture stdout and stderr
    output=$(eval "$cmd" 2>&1)

    # Extract the summary block: from line starting with "Trades" to line starting with "Worst Trade"
    # Using awk to print lines between these patterns (inclusive)
    summary=$(echo "$output" | awk '/^  Trades      :/,/^  Worst Trade :/')

    if [ -n "$summary" ]; then
        echo "$summary"
    else
        echo "No summary block found."
    fi

    echo # blank line between results
done
