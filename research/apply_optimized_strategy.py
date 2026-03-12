from pathlib import Path

# Strategy parameters optimized from 1H backtest
OPTIMIZED_PARAMS = {
    "BOT_TIMEFRAME": "1H",
    "BOT_MIN_SCORE": "120",
    "BOT_TRAIL_PCT": "0.01",
    "BOT_MARGIN_USDT": "10.0",
    "BOT_LEVERAGE": "30",
}


def apply_strategy():
    env_path = Path(".env")
    if not env_path.exists():
        # Create .env with optimized parameters if it doesn't exist
        lines = [f"{k}={v}" for k, v in OPTIMIZED_PARAMS.items()]
        env_path.write_text("\n".join(lines) + "\n")
        print("Created .env with optimized strategy parameters.")
    else:
        # Update existing .env
        content = env_path.read_text()
        lines = content.splitlines()
        new_lines = []
        applied_keys = set()

        for line in lines:
            if "=" in line:
                key = line.split("=")[0].strip()
                if key in OPTIMIZED_PARAMS:
                    new_lines.append(f"{key}={OPTIMIZED_PARAMS[key]}")
                    applied_keys.add(key)
                    continue
            new_lines.append(line)

        for key, value in OPTIMIZED_PARAMS.items():
            if key not in applied_keys:
                new_lines.append(f"{key}={value}")

        env_path.write_text("\n".join(new_lines) + "\n")
        print("Updated .env with optimized strategy parameters.")


if __name__ == "__main__":
    apply_strategy()
