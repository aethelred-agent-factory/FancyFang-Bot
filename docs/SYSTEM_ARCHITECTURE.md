# FancyFangBot System Architecture

**Current Strategy Profile:** Meme Reaper (v2.1)
**Focus:** Macro-Regime Liquidation Hunting (4H SHORT)

---

## 1. Core Architectural Layers

### Layer 1: Connectivity Fabric (`phemex_common.py`)
*   **Token-Bucket Limiter**: Classifies every URL (Contract vs. Others) and manages weights to prevent 429 errors.
*   **Hybrid Data Feed**: REST for historical scanning; WebSockets for sub-millisecond price updates on open positions.
*   **Indicator Suite**: Centralized logic for RSI, Bollinger Bands, EMA slopes, and ATR.

### Layer 2: Signal Scanners (`phemex_long/short.py`)
*   **Multi-Factor Scoring**: Weighted signals combining RSI rollover, BB upper contact, Bearish Divergence, and HTF alignment.
*   **Adaptive Filter**: A dynamic brain that raises `min_score` during high-volatility market-wide pumps to filter out noise.

### Layer 3: Execution Engines (`sim_bot.py` & `p_bot.py`)
*   **Multi-Step Verification**: 3-step check over 20–60s to confirm signal validity before entry.
*   **Fast-Track Logic**: Bypasses scan intervals for "God Signals" to capture the start of liquidation cascades.

### Layer 4: Defense Suite
*   **Risk Manager**: Dynamic Kelly Criterion based on ATR and signal strength.
*   **Drawdown Guard**: Equity circuit-breaker that halts the system if daily loss limits (e.g., 5%) are hit.
*   **Correlation Manager**: Prevents over-exposure to symbols moving in lockstep.

---

## 2. The "Meme Reaper" Strategy Logic

*   **Regime**: Bearish / Overextended Macro.
*   **Timeframe**: 4-Hour (4H).
*   **Entry Logic**: 
    *   Target coins diverged significantly from 200 EMA.
    *   Wait for "Rollover" (RSI peak + BB Upper contact).
*   **Exit Logic**:
    *   **Take Profit**: 50% (Targeting total collapse).
    *   **Trailing Stop**: 5.0% (Wide enough to survive squeezes, tight enough to lock in 30% gains).

---

## 3. Core Strategic Guards (Active)

### A. Funding Rate Filter
*   **Status**: ACTIVE
*   **Logic**: Only allow SHORT entries if Funding Rate is positive.
*   **Impact**: Adds "Carry Trade" income and ensures shorting only when longs are overwhelmingly crowded.

### B. Volume-Price Divergence (VPD)
*   **Status**: ACTIVE
*   **Logic**: Detects "Decreasing Volume on Increasing Price" (Hollow Pump).
*   **Impact**: High-probability signal for a 4H trend reversal.

### C. Weekend Guard
*   **Status**: ACTIVE
*   **Logic**: Automatically increases `min_score` threshold by 25% during Saturday/Sunday UTC.
*   **Impact**: Protects against low-liquidity weekend "scam pumps."

---

## 4. Professional Operational Tips

1.  **Trust the 4H Patience**: Do not lower scores to force trades. Quality liquidations are rare but highly profitable.
2.  **Audit Logs are Truth**: Check `data/logs/system_audit.log` to see exactly why trades were filtered (Correlation, Verification failures, etc.).
3.  **Absolute Baseline**: After a reset, let the bot build its `paper_account.json` naturally. Avoid manual balance edits.
4.  **No Manual Intervention**: Closing positions manually on the exchange breaks the bot's internal PnL tracking. Stop the bot first if manual exit is required.

---
*Documented on: Tuesday, March 10, 2026*
