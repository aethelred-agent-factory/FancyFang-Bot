# FancyFangBot: The Comprehensive Guide

FancyFangBot is a high-frequency, multi-strategy algorithmic trading system specialized for Phemex USDT-M Perpetual futures. It combines traditional technical analysis with modern machine learning (XGBoost & LSTM) and an LLM-driven "Council of Agents" (VoltAgent) for decision validation.

---

## 1. System Architecture

The system is designed in hierarchical layers, ensuring robustness and separation of concerns.

### Layer 1: Connectivity & Infrastructure (`core/phemex_common.py`)
- **API Management:** A weighted token-bucket rate limiter prevents HTTP 429 errors by classifying endpoints (Market vs. Contract).
- **Hybrid Data Feed:** Uses REST for broad market scanning and WebSockets for sub-millisecond price updates on active positions.
- **Indicators:** Centralized, high-performance implementation of RSI, Bollinger Bands, EMAs, ATR, ADX, and Volume Profile (POC).
- **Denoising:** Implements a Kalman Filter to smooth price action and reveal underlying trends.

### Layer 2: Analysis & Signal Generation (`core/phemex_long.py`, `core/phemex_short.py`)
- **Multi-Factor Scoring:** Each candidate is scored (0–250) based on weighted signals:
    - RSI Oversold/Overbought & Rollovers.
    - Bollinger Band boundary contact.
    - EMA Stretches (Mean Reversion) & EMA 200 Macro Divergence.
    - Bullish/Bearish Divergence detection.
    - Pattern Recognition (Hammer, Morning Star, Engulfing, etc.).
- **Alpha Enhancements:** Includes ADX trend filters, Volume Profile proximity, and Market Regime detection (Trending, Ranging, Volatile).

### Layer 3: Execution Engines (`core/p_bot.py`, `core/sim_bot.py`)
- **Live Engine (`p_bot`):** Handles authenticated execution on Phemex.
- **Simulation Engine (`sim_bot`):** Paper trading with realistic slippage simulation and a dedicated TUI.
- **Verification Loop:** Implements a 3-step verification process (waiting ~20s) to confirm signal stability before committing capital.
- **Fast-Track Logic:** Allows immediate entry for exceptionally high-score signals.

### Layer 4: Defensive Suite (`modules/`)
- **Drawdown Guard:** A circuit-breaker that halts trading if daily loss limits are exceeded.
- **Correlation Manager:** Prevents over-exposure by limiting entries into assets moving in lockstep.
- **Liquidity Spectre:** Filters out low-liquidity assets to minimize slippage.
- **Failure Guard:** Uses a trained ML model to predict and block high-risk candidates based on historical failure modes.

---

## 2. Advanced Features

### Machine Learning Stack
- **Ensemble Scorer:** Combines an **XGBoost Classifier** (tabular features) and a **PyTorch LSTM** (60-candle sequences) to produce a predictive "Win Probability."
- **Feature Builder:** Generates 50+ normalized features including Shannon Entropy, Hurst Exponents, and funding rate momentum.
- **Automated Retraining:** The system automatically exports data and retrains models after every 50 narrated trades, ensuring the bot adapts to shifting market regimes.

### LLM & Agent Integration (VoltAgent)
- **Trade Narrator:** After a trade closes, the bot uses DeepSeek LLM to "narrate" the trade, identifying the primary driver and failure mode.
- **Council of Agents:** Integration with a TypeScript-based agent supervisor (`voltagent/`) that provides secondary validation for entries and performs market sentiment analysis.
- **Event-Driven Coordination:** Uses an internal `EventEmitter` to sync state between the Python bot and the TS agents.

### User Interface & Visualization
- **TUI Dashboard:** A rich terminal interface featuring:
    - Real-time PnL Sparklines.
    - Cyber Telemetry (Equity, Margin, Entropy bars).
    - Live trade monitor with Stop-Loss/Take-Profit "Price Lines."
    - Rolling event logs.
- **Telegram Control:** Full remote control via Telegram bot for scanning, checking balance, closing positions, and receiving alerts.

---

## 3. The "Meme Reaper" Strategy

The current flagship strategy profile (v2.1) focuses on **Macro-Regime Liquidation Hunting**:
- **Timeframe:** 4-Hour (4H) for signal quality.
- **Core Logic:** Target extreme macro-divergence from the 200 EMA.
- **Funding Filter:** Prefers SHORTs when funding is positive (crowded longs).
- **VPD (Volume-Price Divergence):** Detects "Hollow Pumps" where price increases on declining volume.
- **Weekend Guard:** Automatically tightens entry requirements during low-liquidity weekend hours.

---

## 4. How to Use

### Installation
1. Install dependencies: `pip install -r requirements.txt`
2. Configure environment: `cp env.example .env` and fill in your API keys.

### Running the Bot
- **Simulation (Paper Trading):**
  ```bash
  python3 core/sim_bot.py
  ```
- **Live Trading (Use Caution):**
  ```bash
  python3 core/p_bot.py
  ```
- **Backtesting:**
  ```bash
  python3 research/backtest.py --symbol BTCUSDT --timeframe 15m --candles 1000
  ```

### Key Commands (in TUI)
- `[O]`: Trigger manual market scan.
- `[S]`: Emergency close all positions.
- `[Q]`: Safe shutdown.

### Research & ML Pipeline
1. **Export Data:** `python3 research/export_training_data.py`
2. **Train Models:** 
   - `python3 research/train_classifier.py`
   - `python3 research/train_lstm.py`
3. **Optimize Parameters:** `python3 research/param_optimizer.py`

---

## 5. Directory Structure at a Glance

- `core/`: Primary execution logic and exchange infrastructure.
- `modules/`: Specialized risk, analytics, and agent integration modules.
- `research/`: Scripts for backtesting, optimization, and ML training.
- `models/`: Saved XGBoost and PyTorch model files.
- `data/`: Local database (`fancybot_sim.db`), logs, and state files.
- `voltagent/`: TypeScript supervisor and agent council implementation.
- `docs/`: In-depth documentation and caretaker journals.

---
*Generated: March 12, 2026*
