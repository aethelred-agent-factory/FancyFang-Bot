#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  FancyFangBot                            ║
# ║                                                                              ║
# ║  This file, and every file in this project, was written entirely through     ║
# ║  iterative AI prompting (Claude / Anthropic). No lines were written by       ║
# ║  hand. All architecture decisions, refactors, bug fixes, and feature         ║
# ║  additions were directed via natural-language prompts and implemented by     ║
# ║  AI. This is expected to remain the primary (and likely only) development    ║
# ║  method for this project for the foreseeable future.                         ║
# ║                                                                              ║
# ║  If you are a human developer reading this: the design intent and business   ║
# ║  logic live in the prompt history, not in comments. Treat this code as you   ║
# ║  would any LLM output — verify critical paths before trusting them.          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
"""
Phemex Short Setup Scanner — USDT-M Perpetuals
----------------------------------------------
Optimized short setup scanner for Phemex USDT-M Perpetuals.

Short bias logic:
  - RSI overbought / rolling over from highs (> 65 / 55-75 rollover zone)
  - Price at/above BB upper band
  - Price above EMA21 (mean reversion) or EMA21 turning down (trend continuation)
  - Positive funding (crowded longs → fade fuel)
  - Near 24h HIGH (not low)
  - Bearish candle patterns (shooting star, evening star, engulfing, etc.)
  - Bearish divergence (price makes higher high, RSI makes lower high)
  - Pump + dump setups, blow-off tops
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import logging
import os
from typing import Any, List, Optional, Tuple

import core.phemex_common as pc
from colorama import Fore, init
from dotenv import load_dotenv
from modules.feature_builder import FeatureBuilder
import numpy as np

# Initialize environment & colorama
load_dotenv()
init(autoreset=True)

# Instantiate the builders
feature_builder_short = FeatureBuilder()


# ----------------------------
# CONFIG & EXPORTS
# ----------------------------
__all__ = [
    "analyse",
    "get_tickers",
    "get_candles",
    "prefetch_all_funding_rates",
    "BASE_URL",
    "TIMEFRAME_MAP",
    "DEFAULTS",
]

BASE_URL = pc.BASE_URL
TIMEFRAME_MAP = pc.TIMEFRAME_MAP
DEFAULTS = pc.DEFAULTS

# Strategy thresholds
MIN_NEGATIVE_FUNDING = (
    -0.02
)  # skip if funding too negative (crowded shorts = bad for shorts)
THREE_BLACK_CROWS_RSI_GATE = 45
DIVERGENCE_WINDOW = 60
DIV_PRICE_THRESHOLD = 1.005  # 0.5% higher high
DIV_RSI_THRESHOLD = 3.0  # 3.0 RSI points lower high
RSI_OVERSOLD_ZONE = 35.0
RSI_OVERBOUGHT_ZONE = 65.0

# Score weights
WEIGHTS = {
    "divergence": 20,
    "rsi_rollover": 25,  # RSI rolling over from highs
    "rsi_overbought": 22,  # deep overbought reading
    "bb_upper_90": 30,  # price above/at BB upper
    "bb_upper_75": 22,  # price near BB upper
    "ema_stretch_3": 15,  # price significantly above EMA21 (mean reversion)
    "ema_stretch_200": 25,  # price significantly above EMA200 (Macro divergence)
    "vol_spike_2": 15,  # volume spike
    "funding_high": 22,  # positive funding = crowded longs = fade fuel
    "htf_align_overbought": 15,  # 1H RSI overbought confirms LTF short
    "funding_momentum": 10,  # funding becoming more positive (building fade)
}

TRADE_LOG_FILE = os.path.dirname(os.path.abspath(__file__)) + "/trade_log_short.json"
SCAN_OUTPUT_FILE = os.path.dirname(os.path.abspath(__file__)) + "/last_scan_short.json"

logger = logging.getLogger("phemex_short_scanner")
logger.addHandler(logging.NullHandler())

# ----------------------------
# Data classes
# ----------------------------
# [T3-05] Use the canonical TickerData from core.phemex_common rather than a local
# trimmed copy.  The local definition was missing rsi_4h, kalman_slope, entropy,
# and dist_low_pct — fields populated by unified_analyse and used by scoring /
# confidence functions added in the upgrade pass.
TickerData = pc.TickerData


# ----------------------------
# Indicator Logic
# ----------------------------
def find_peaks(values: List[float], min_separation: int = 3) -> List[int]:
    """Local peak finder for bearish divergence."""
    peaks: List[int] = []
    n = len(values)
    if n < 3:
        return peaks
    for i in range(1, n - 1):
        if values[i] > values[i - 1] and values[i] > values[i + 1]:
            if not peaks or (i - peaks[-1] >= min_separation):
                peaks.append(i)
    return peaks


def detect_bearish_divergence(
    closes: List[float], rsi_values: List[Optional[float]]
) -> bool:
    """
    Bearish divergence: price makes a higher high while RSI makes a lower high.
    This signals exhaustion of buyers and a likely reversal downward.
    """
    if len(closes) < DIVERGENCE_WINDOW or len(rsi_values) < DIVERGENCE_WINDOW:
        return False
    price_window = pc.np.asarray(closes[-DIVERGENCE_WINDOW:], dtype=float)
    rsi_window_list = rsi_values[-DIVERGENCE_WINDOW:]
    if any(v is None for v in rsi_window_list):
        return False
    rsi_window = pc.np.asarray([float(v) for v in rsi_window_list], dtype=float)

    price_peaks = find_peaks(price_window.tolist())
    rsi_peaks = find_peaks(rsi_window.tolist())

    if len(price_peaks) < 2 or len(rsi_peaks) < 2:
        return False

    # Time-alignment check — ensure price and RSI peaks occur within 5 candles of each other
    if (
        abs(price_peaks[-1] - rsi_peaks[-1]) > 5
        or abs(price_peaks[-2] - rsi_peaks[-2]) > 5
    ):
        return False

    p1 = price_window[price_peaks[-2]]
    p2 = price_window[price_peaks[-1]]
    r1 = rsi_window[rsi_peaks[-2]]
    r2 = rsi_window[rsi_peaks[-1]]

    # Apply stricter thresholds and ensure second RSI peak is in overbought zone
    return (
        (p2 > p1 * DIV_PRICE_THRESHOLD)
        and (r2 < r1 - DIV_RSI_THRESHOLD)
        and (r2 > RSI_OVERBOUGHT_ZONE - 10)
    )


def detect_patterns(ohlc: List[Tuple[float, float, float, float]]) -> List:
    """Detect bearish reversal / continuation candle patterns."""
    patterns = []
    if len(ohlc) < 3:
        return patterns

    def body(c):
        return abs(c[3] - c[0])

    def upper_wick(c):
        return c[1] - max(c[0], c[3])

    def lower_wick(c):
        return min(c[0], c[3]) - c[2]

    def is_bear(c):
        return c[3] < c[0]

    def is_bull(c):
        return c[3] > c[0]

    c0, c1, c2 = ohlc[-3], ohlc[-2], ohlc[-1]

    # Shooting Star — long upper wick, small body, at high area
    if (
        upper_wick(c2) > 2 * body(c2)
        and lower_wick(c2) < body(c2) * 0.4
        and body(c2) > 0
    ):
        patterns.append(("Shooting Star 🌠", 15, 1.0))

    # Gravestone Doji at high (supply spike)
    if (
        upper_wick(c2) > 2.5 * body(c2)
        and body(c2) < (c2[1] - c2[2]) * 0.2
        and c2[1] > c1[1]
    ):
        patterns.append(("Gravestone Doji ⚰️", 14, 1.0))

    # Bearish Engulfing — bull candle followed by larger bear candle
    if (
        is_bull(c1)
        and is_bear(c2)
        and c2[3] <= c1[0]
        and c2[0] >= c1[3]
        and body(c2) > body(c1)
    ):
        patterns.append(("Bearish Engulfing 🔴", 18, 1.0))

    # Evening Star — bull, small body (indecision), bear (3-candle reversal)
    if (
        is_bull(c0)
        and body(c1) < body(c0) * 0.5
        and is_bear(c2)
        and c2[3] < (c0[0] + c0[3]) / 2
    ):
        patterns.append(("Evening Star 🌙", 20, 1.0))

    # Dark Cloud Cover — bull candle, bear opens above high, closes below midpoint
    if (
        is_bull(c1)
        and is_bear(c2)
        and c2[0] > c1[1]
        and c2[3] < (c1[0] + c1[3]) / 2
        and c2[3] > c1[0]
    ):
        patterns.append(("Dark Cloud Cover ☁️", 16, 1.0))

    # Bearish Harami — bull followed by smaller bear inside it
    if (
        is_bull(c1)
        and is_bear(c2)
        and c2[0] < c1[3]
        and c2[3] > c1[0]
        and body(c2) < body(c1)
    ):
        patterns.append(("Bearish Harami 🟥", 12, 1.0))

    # Doji at High — indecision after uptrend (reversal warning)
    if body(c2) < (c2[1] - c2[2]) * 0.15 and c2[1] > c1[1]:
        patterns.append(("Doji at High — Reversal Watch 🔄", 10, 1.0))

    # Three Black Crows — three consecutive bear candles
    if (
        is_bear(c0)
        and is_bear(c1)
        and is_bear(c2)
        and c1[3] < c0[3]
        and c2[3] < c1[3]
        and body(c0) > 0
        and body(c1) > 0
        and body(c2) > 0
    ):
        patterns.append(("Three Black Crows 🐦‍⬛", 18, 1.0))

    # Bearish Marubozu — strong bear with almost no wicks (momentum)
    if (
        is_bear(c2)
        and upper_wick(c2) < body(c2) * 0.1
        and lower_wick(c2) < body(c2) * 0.1
        and body(c2) > (c2[1] - c2[2]) * 0.85
    ):
        patterns.append(("Bearish Marubozu 📉", 14, 1.0))

    return patterns


# ----------------------------
# Confidence & Scoring — SHORT BIASED
# ----------------------------
def calc_confidence(
    rsi,
    bb_pct,
    ema21,
    price,
    change_24h,
    funding_rate,
    patterns,
    score,
    dist_high_pct,
    vol_spike,
    ema200=None,
):
    """
    Short-biased confidence: counts bearish agreeing signals vs bullish conflicts.
    """
    agreeing = 0.0
    conflicts = 0.0
    notes: List[str] = []

    if rsi is not None:
        if rsi > 55.0:
            agreeing += 1.0
        elif rsi < 35.0:
            conflicts += 1.0
            notes.append("RSI oversold — late entry risk")

    if bb_pct is not None:
        if bb_pct >= 65.0:
            agreeing += 1.0
        elif bb_pct < 30.0:
            conflicts += 1.0
            notes.append("price below BB 30%")

    if ema21 is not None and price is not None:
        pct = pc.pct_change(price, ema21)
        if pct > 1.0:
            agreeing += 1.0
        elif pct < -2.0:
            conflicts += 0.5

    if ema200 is not None and price is not None:
        pct_200 = pc.pct_change(price, ema200)
        if pct_200 > 2.0:
            agreeing += 1.0

    if change_24h is not None:
        if 3.0 <= change_24h <= 15.0:
            agreeing += 1.0
        elif change_24h > 15.0:
            agreeing += 0.5
        elif change_24h < -5.0:
            conflicts += 1.0
            notes.append("dumping already")
        elif -0.5 < change_24h < 0.5:
            conflicts += 0.5
            notes.append("flat — no momentum")

    if funding_rate is not None:
        fr_pct = funding_rate * 100.0
        if fr_pct > 0.01:
            agreeing += 1.0
        elif fr_pct < -0.05:
            conflicts += 2.0
            notes.append("crowded shorts")

    if dist_high_pct is not None and dist_high_pct < 1.0:
        agreeing += 1.0

    if vol_spike > 1.5:
        agreeing += 1.0

    if patterns:
        agreeing += 1.0

    net = agreeing - conflicts
    if net >= 4.0 and score >= 60:
        return "HIGH", Fore.GREEN, notes
    if net >= 2.0 and score >= 40:
        return "MEDIUM", Fore.YELLOW, notes
    return "LOW", Fore.RED, notes


def _calc_confidence_adapter(data: pc.TickerData, score: int, bb_pct) -> tuple:
    """
    Adapter so unified_analyse can call calc_confidence with the expected
    (TickerData, score, bb_pct) signature.
    [T1-02] Bridges the legacy positional signature to the unified calling convention.
    """
    return calc_confidence(
        data.rsi,
        bb_pct,
        data.ema21,
        data.price,
        data.change_24h,
        data.funding_rate,
        data.patterns,
        score,
        data.dist_high_pct,
        data.vol_spike,
        ema200=data.ema200,
    )


def score_short(data: TickerData) -> Tuple[int, List[str]]:
    """
    Aggregate a score for a SHORT setup using both legacy logic and the new
    FeatureBuilder / PredictionEngine.
    Returns a compatibility integer score and a list of signals.
    """
    score = 0
    signals: List[str] = []

    # 1. RSI Logic
    if data.rsi is not None:
        if data.rsi > 75.0:
            score += WEIGHTS["rsi_overbought"]
            signals.append(f"Deep Overbought RSI ({data.rsi:.1f})")
        elif data.rsi > RSI_OVERBOUGHT_ZONE:
            score += WEIGHTS["rsi_rollover"]
            signals.append(f"Overbought RSI ({data.rsi:.1f})")

        if data.prev_rsi is not None and data.rsi < data.prev_rsi and data.rsi > 55.0:
            score += 10
            signals.append("RSI Rolling Over")

    # 2. Bollinger Band Logic
    if data.bb is not None and data.price is not None:
        bb_range = data.bb["upper"] - data.bb["lower"]
        if bb_range > 0:
            bb_pct = (data.price - data.bb["lower"]) / bb_range
            if bb_pct > 0.90:
                score += WEIGHTS["bb_upper_90"]
                signals.append("Price at/above BB Upper")
            elif bb_pct > 0.75:
                score += WEIGHTS["bb_upper_75"]
                signals.append("Price near BB Upper")

    # 3. EMA Stretch
    if data.ema21 is not None and data.price is not None:
        pct_diff = pc.pct_change(data.price, data.ema21)
        if pct_diff > 3.0:
            score += WEIGHTS["ema_stretch_3"]
            signals.append(f"Mean Reversion Stretch ({pct_diff:.1f}%)")

    if data.ema200 is not None and data.price is not None:
        pct_diff_200 = pc.pct_change(data.price, data.ema200)
        if pct_diff_200 > 3.0:
            score += WEIGHTS["ema_stretch_200"]
            signals.append(f"Macro Divergence (EMA200 +{pct_diff_200:.1f}%)")

    # 4. Funding logic
    if data.funding_rate is not None:
        if data.funding_rate > 0.0001:  # 0.01%
            score += WEIGHTS["funding_high"]
            signals.append(f"High Positive Funding ({data.funding_rate*100:.3f}%)")

    # 5. Bearish Divergence
    if data.has_div:
        score += WEIGHTS["divergence"]
        signals.append("Bearish Divergence")

    # 6. Patterns
    for name, p_score, p_conf in data.patterns:
        score += p_score
        signals.append(f"Pattern: {name}")

    # 7. Volume-Price Divergence (VPD) — Hollow Pump detection
    if len(data.raw_ohlc) >= 5:
        recent_window = data.raw_ohlc[-5:]
        prices = [c[3] for c in recent_window]  # Closes
        vols = [c[4] for c in recent_window]  # Volumes

        price_increasing = all(
            prices[i] >= prices[i - 1] for i in range(1, len(prices))
        )
        volume_decreasing = all(vols[i] <= vols[i - 1] for i in range(1, len(vols)))

        if price_increasing and volume_decreasing:
            score += 20
            signals.append("Volume-Price Divergence (Hollow Pump)")

    # ── Alpha Enhancements ───────────────────────────────────────────────────
    # 1. ADX Filter
    if data.adx is not None:
        if data.adx > 25:
            # Strong trend. Check if EMA slope agrees.
            if data.ema_slope is not None and data.ema_slope < 0:
                score += 15
                signals.append(f"Strong Bearish Trend (ADX:{data.adx:.1f})")
        elif data.adx < 20:
            # Weak trend, mean-reversion signals are better here.
            score += 10
            signals.append(
                f"Ranging Market — Mean Reversion Favored (ADX:{data.adx:.1f})"
            )

    # 2. POC Distance (Volume Profile)
    if data.poc_price is not None and data.price is not None:
        dist_to_poc = pc.pct_change(data.price, data.poc_price)
        if dist_to_poc > 2.0:  # Price is significantly above POC
            score += 12
            signals.append(
                f"Price Above POC ({dist_to_poc:.1f}%) — Gravitational pull down"
            )

    # 3. Regime Scaling
    if data.regime == "VOLATILE":
        score -= 20
        signals.append("Regime: Volatile — High Risk Penalty")
    elif (
        data.regime == "TRENDING" and data.ema_slope is not None and data.ema_slope < 0
    ):
        score += 10
        signals.append("Regime: Bearish Trending")

    # ── New Predictive / Ensemble Integration ───────────────────────────────
    features = feature_builder_short.build_features(data, getattr(data, 'market_context', {}))
    data.ml_features = features
    
    predictive_score = pc.score_func(data, "SHORT")

    # Scale predictive score into the 0-200 range logic
    predictive_bonus = int(predictive_score * 30)
    score += predictive_bonus

    signals.append(f"PREDICTIVE_SCORE_RAW:{predictive_score:.4f}")
    if predictive_score > 0.5:
        signals.append("PREDICTIVE: STRONG BEARISH BIAS")

    # Final cap for compatibility
    final_score = max(0, min(250, score))
    return final_score, signals


# ----------------------------
# Proxies
# ----------------------------
def get_tickers(rps: float = None) -> List[dict]:
    return pc.get_tickers(rps)


def get_candles(
    symbol: str, timeframe: str = "15m", limit: int = 100, rps: float = None
) -> List[List[Any]]:
    return pc.get_candles(symbol, timeframe, limit, rps)


prefetch_all_funding_rates = pc.prefetch_all_funding_rates


# ----------------------------
# Main Analysis
# ----------------------------
def analyse(
    ticker: dict,
    cfg: dict,
    enable_ai: bool = True,
    enable_entity: bool = True,
    scan_id: str = None,
) -> dict | None:
    """
    Analyse a single Phemex USDT-M perpetual ticker for SHORT setups.

    [T1-02] Delegates to pc.unified_analyse(), which is the single source of
    truth for signal generation.  All upgrade logic (slippage model Upgrade #1,
    volatility filter Upgrade #6, order book imbalance Upgrade #10) is now
    active on every scan call rather than being silently orphaned in a function
    that was never called from production code.

    Direction-specific behaviour is injected via callbacks:
      score_func           → score_short
      detect_patterns_func → detect_patterns (bearish patterns)
      detect_div_func      → detect_bearish_divergence
      calc_confidence_func → _calc_confidence_adapter (wraps calc_confidence)

    A pre-score gate (default 60) is applied inside unified_analyse after the
    cheap indicator pass; symbols below the threshold skip the expensive
    order-book / HTF-candle / volume-profile API calls.
    """
    result = pc.unified_analyse(
        ticker=ticker,
        cfg=cfg,
        direction="SHORT",
        score_func=score_short,
        detect_patterns_func=detect_patterns,
        detect_div_func=detect_bearish_divergence,
        calc_confidence_func=_calc_confidence_adapter,
        enable_ai=enable_ai,
        enable_entity=enable_entity,
        scan_id=scan_id,
    )
    if result:
        # Extract the actual predictive score that was embedded in signals.
        predictive_score_str = next(
            (s for s in result["signals"] if s.startswith("PREDICTIVE_SCORE_RAW:")),
            None,
        )
        if predictive_score_str:
            actual_predictive_score = float(predictive_score_str.split(":")[1].strip())
            result["predictive_score"] = actual_predictive_score
            # Remove this string from signals list, as it has its own dedicated field now
            result["signals"] = [
                s
                for s in result["signals"]
                if not s.startswith("PREDICTIVE_SCORE_RAW:")
            ]
        else:
            result["predictive_score"] = 0.0  # Default if not found
    return result
