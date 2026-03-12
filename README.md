# FancyFangBot
A Phemex perpetual-futures algorithmic trading bot with paper simulation,
live execution, walk-forward backtesting, and a full-screen TUI dashboard.

---

## Quickstart

- Clone the repo:

  ```bash
  git clone <repo-url>
  cd fancybot_revised
  ```
- Create a Python virtual environment and install requirements:

  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```
- Copy and edit environment variables from the example:

  ```bash
  cp env.example .env
  # edit .env with API keys and tuning values
  ```

---

## Overview & Important Files

- `phemex_common.py` — core helpers, indicators, filters
- `sim_bot.py` — paper trading simulation
- `p_bot.py` — live-trading bot (Phemex authenticated)
- `backtest.py` — walk-forward backtester
- `risk_manager.py`, `drawdown_guard.py`, `signal_analytics.py` — risk and analytics modules
- `param_optimizer.py` — parameter grid-search and optimizer output
- `telegram_controller.py` — optional Telegram control interface

Auto-created/runtime files: `paper_account.json`, `sim_trade_results.json`, `signal_analytics.json`, `optimizer_results.json`.

---

## Usage Examples

- Run a quick backtest (15m candles):

  ```bash
  python3 backtest.py --timeframe 15m --candles 500 --min-score 120
  ```

- Run the simulation bot (paper):

  ```bash
  python3 sim_bot.py --no-ai --no-entity --interval 60
  ```

- Start the live bot (ensure `.env` has keys and `PHEMEX_API_KEY`/`PHEMEX_API_SECRET`):

  ```bash
  python3 p_bot.py
  ```

---

## Configuration

Edit `.env` (see `env.example`) to configure risk model, ATR multipliers, spread/volatility filters, Telegram settings, and other runtime knobs.

Key variables include: `RISK_MODEL`, `FIXED_RISK_PER_TRADE`, `RISK_PCT_PER_TRADE`, `MAX_PORTFOLIO_RISK`, `ATR_STOP_MULT`, `ATR_TRAIL_MULT`, `SPREAD_FILTER_MAX_PCT`, `VOLATILITY_FILTER_MIN`, `MAX_DAILY_DRAWDOWN`, `TG_BOT_TOKEN`, `TG_CHAT_ID`.

---

## Testing

Run the project's unit tests with `pytest`:

```bash
pytest -q
```

You can also run small module checks directly (examples):

```bash
python3 -c "import drawdown_guard as dd; dd.set_start_balance(100.0); dd.record_pnl(-3.0,97.0); print(dd.can_open_trade())"
```

---

## Contributing

1. Fork the repo and create a feature branch.
2. Follow existing code style and add tests for new behavior.
3. Open a pull request describing the change and rationale.

If you're changing risk-related code, include backtest results and rationale in the PR description.

---

## License

This repository does not include a license file. Add one if you plan to publish or share.

---

## Full Module Notes

The project includes multiple upgrades and helper modules (slippage, ATR stops, spread/vol filters, signal normalisation, parameter optimizer, dynamic risk manager, Telegram control). See individual modules for implementation details and configuration options.

For a thorough description of implemented upgrades and examples, keep the existing module-level docs and comments as the authoritative source.
