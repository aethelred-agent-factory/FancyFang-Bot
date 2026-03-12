# FancyFangBot

A Phemex perpetual-futures algorithmic trading bot with paper simulation,
live execution, walk-forward backtesting, and a full-screen TUI dashboard.

---

## Project Structure

```
FancyFangBot/
├── phemex_common.py        ★ Core: indicators, API helpers, NEW filter functions
├── phemex_long.py           Long scanner / scoring
├── phemex_short.py          Short scanner / scoring
├── phemex_scanner.py        Dual-direction batch scanner
├── p_bot.py                 Live-trading bot (Phemex authenticated)
├── sim_bot.py              ★ Paper-trading simulation bot (upgrade entry-point)
├── backtest.py             ★ Walk-forward backtester (ATR stops + filters added)
├── ui.py                    Standalone TUI dashboard
│
├── signal_analytics.py     ★ NEW — per-signal performance statistics (Upgrade #8)
├── risk_manager.py         ★ NEW — dynamic/adaptive position sizing (Upgrade #12)
├── drawdown_guard.py       ★ NEW — daily drawdown kill switch (Upgrade #7)
├── param_optimizer.py      ★ NEW — lightweight parameter grid-search (Upgrade #11)
├── telegram_controller.py  ★ NEW — Telegram bot control interface (Upgrade #13)
│
├── paper_account.json       Auto-created sim account state
├── sim_trade_results.json   Trade log (auto-created)
├── signal_analytics.json    Signal stats (auto-created)
├── optimizer_results.json   Optimizer output (auto-created)
└── .env                     API keys and tuning knobs
```

---

## Upgrades Implemented

### #1 Realistic Slippage Simulation
phemex_common.py: calc_slippage()
sim_bot.py: execute_sim_setup()

If bid/ask available: slippage = (ask-bid)/2 x factor.
Fallback: ATR x 0.01 or price x 0.0002.
Fill price adjusted before sizing. Logged as event_type="FILL".

### #2 ATR-Based Stop-Loss and Trailing Stop
phemex_common.py: calc_atr_stops(), update_atr_trail()
sim_bot.py: execute_sim_setup(), update_pnl_and_stops()
backtest.py: entry logic

stop_distance = ATR(14) x ATR_STOP_MULT (default 1.5)
trail_distance = ATR(14) x ATR_TRAIL_MULT (default 1.0)
Falls back to percentage-based when ATR unavailable.

### #3 Spread Filter
phemex_common.py: check_spread_filter()

Skips trades when spread_pct > SPREAD_FILTER_MAX_PCT (default 0.10%).
Logged with event_type="SKIP".

### #4 Z-Score Signal Normalisation
phemex_common.py: RollingNormalizer, calc_normalised_composite_score()

Rolling 50-bar z-score on EMA slope, volume spike, RSI change.
score = 0.4*trend_z + 0.3*volume_z + 0.3*momentum_z
Available for scanner customisation; does not replace existing scoring.

### #5 Multiple Concurrent Positions
sim_bot.py: execute_sim_setup()

Checks len(positions) >= dynamic_max via risk_manager.should_reject_trade().
Dynamic max scales with account balance via SCALING_TIERS.

### #6 Volatility Filter
phemex_common.py: check_volatility_filter()

Skips when ATR/price < VOLATILITY_FILTER_MIN (default 0.002).
Prevents choppy/low-range market entries.

### #7 Daily Drawdown Kill Switch
drawdown_guard.py (new module)

Blocks new entries when daily loss >= MAX_DAILY_DRAWDOWN (default 5%).
Existing positions can still close. Resets at UTC midnight.

### #8 Signal Performance Statistics
signal_analytics.py (new module)

Records every closed trade against its signal types.
Per-signal: win_rate, avg_return, expectancy, profit_factor.
Data in signal_analytics.json. Use print_signal_report() for output.

### #9 Dynamic Pair Selection
phemex_common.py: select_top_pairs()

Pre-filters tickers by volume and daily range volatility.
Composite rank: 0.6*log(volume) + 0.4*ATR%.

### #10 Order Book Imbalance Signal
phemex_common.py: calc_order_book_imbalance(), get_order_book_with_volumes()

imbalance = bid_volume / ask_volume (top 5 levels).
Returned as ob_imbalance in every scanner result dict.

### #11 Parameter Optimization Framework
param_optimizer.py (new module)

Grid search over: atr_stop_mult, atr_trail_mult, score_threshold,
spread_max_pct, vol_min.
Metrics: PnL, win_rate, profit_factor, max_drawdown, Sharpe, expectancy.
Results saved to optimizer_results.json.

### #12 Portfolio Risk Manager
risk_manager.py (new module)

RISK_MODEL options (set via env):
  fixed_usd           — always risk FIXED_RISK_PER_TRADE USD
  percent_of_account  — risk RISK_PCT_PER_TRADE % of balance
  dynamic_kelly       — half-Kelly x signal confidence (default)

Adaptive scaling: small accounts take higher %, large accounts lower %.
Portfolio cap: current_risk + new_risk <= balance x MAX_PORTFOLIO_RISK (30%).

### #13 Telegram Control Interface
telegram_controller.py (new module)

Commands: /start /stop /status /profit /positions
Started automatically inside sim_bot.py when TG_BOT_TOKEN and TG_CHAT_ID set.

---

## Environment Variables

```
RISK_MODEL=dynamic_kelly
FIXED_RISK_PER_TRADE=1.0
RISK_PCT_PER_TRADE=0.01
MAX_PORTFOLIO_RISK=0.30
MAX_POSITIONS=3
MIN_ACCOUNT_RISK_PCT=0.005
MAX_ACCOUNT_RISK_PCT=0.05
ATR_STOP_MULT=1.5
ATR_TRAIL_MULT=1.0
SPREAD_FILTER_MAX_PCT=0.10
VOLATILITY_FILTER_MIN=0.002
MAX_DAILY_DRAWDOWN=0.05
TG_BOT_TOKEN=
TG_CHAT_ID=
```

---

## Testing

```bash
# Test new modules
python3 -c "
import drawdown_guard as dd
dd.set_start_balance(100.0)
dd.record_pnl(-3.0, 97.0)
dd.record_pnl(-3.0, 94.0)
print(dd.can_open_trade())   # (False, '...')
print(dd.get_status())
"

python3 -c "
import risk_manager as rm
amt, _ = rm.compute_dynamic_risk(100.0, signal_strength=0.7)
print(f'risk_amount={amt:.4f}')
"

python3 -c "
import signal_analytics as sa
sa.record_trade(['RSI Recovery'], 100.0, 102.0, 5.0, 'LONG', 'BTCUSDT')
sa.print_signal_report()
"

# Run backtest with upgrades
python3 backtest.py --timeframe 15m --candles 500 --min-score 120

# Run simulation (check sim_bot.log for SPREAD_FILTER, VOL_FILTER, ATR STOP, SLIPPAGE lines)
python3 sim_bot.py --no-ai --no-entity --interval 60
```
