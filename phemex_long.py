#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  AI-GENERATED CODEBASE — FancyBlenny / fancybot                            ║
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
Phemex Long Setup Scanner — USDT-M Perpetuals
----------------------------------------------
Mirror of phemex_short.py with all signals inverted for long setups.

Long bias logic:
  - RSI oversold / bouncing from lows (< 35 / 25-45 recovery zone)
  - Price at/below BB lower band
  - Price below EMA21 (mean reversion) or EMA21 turning up (trend continuation)
  - Negative funding (crowded shorts → squeeze fuel)
  - Near 24h LOW (not high)
  - Bullish candle patterns (hammer, morning star, engulfing, etc.)
  - Bullish divergence (price makes lower low, RSI makes higher low)
  - Drop + bounce setups, capitulation candles
"""

from __future__ import annotations

import os
import logging
from typing import Any, List, Optional, Tuple

from colorama import init, Fore
from dotenv import load_dotenv

import phemex_common as pc

# Initialize environment & colorama
load_dotenv()
init(autoreset=True)

# ----------------------------
# CONFIG & EXPORTS
# ----------------------------
__all__ = [
    "analyse", "get_tickers", "get_candles", "prefetch_all_funding_rates",
    "BASE_URL", "TIMEFRAME_MAP", "DEFAULTS"
]

BASE_URL = pc.BASE_URL
TIMEFRAME_MAP = pc.TIMEFRAME_MAP
DEFAULTS = pc.DEFAULTS

# Strategy thresholds — long-biased
MAX_POSITIVE_FUNDING = 0.02        # skip if funding too positive (crowded longs = bad for longs)
THREE_WHITE_SOLDIERS_RSI_GATE = 55 # three white soldiers only valid if RSI not already overbought
DIVERGENCE_WINDOW = 60
DIV_PRICE_THRESHOLD = 0.995        # 0.5% lower low
DIV_RSI_THRESHOLD = 3.0            # 3.0 RSI points higher low
RSI_OVERSOLD_ZONE = 35.0
RSI_OVERBOUGHT_ZONE = 65.0

# Score weights (long-biased)
WEIGHTS = {
    "divergence": 20,
    "rsi_recovery": 25,        # RSI bouncing from oversold
    "rsi_oversold": 22,        # deep oversold reading
    "bb_lower_90": 30,         # price below/at BB lower
    "bb_lower_75": 22,         # price near BB lower
    "ema_stretch_3": 15,       # price significantly below EMA21 (mean reversion)
    "vol_spike_2": 15,         # volume spike (capitulation or demand)
    "funding_negative": 22,    # negative funding = crowded shorts = squeeze fuel
    "htf_align_oversold": 15,  # 1H RSI oversold confirms LTF long
    "funding_momentum": 10,    # funding becoming more negative (building squeeze)
}

TRADE_LOG_FILE = os.path.dirname(os.path.abspath(__file__)) + "/trade_log_long.json"
SCAN_OUTPUT_FILE = os.path.dirname(os.path.abspath(__file__)) + "/last_scan_long.json"

logger = logging.getLogger("phemex_long_scanner")
logger.addHandler(logging.NullHandler())

# ----------------------------
# Data classes
# ----------------------------
# [T3-05] Use the canonical TickerData from phemex_common rather than a local
# trimmed copy.  The local definition was missing rsi_4h, kalman_slope, entropy,
# and dist_high_pct — fields populated by unified_analyse and used by scoring /
# confidence functions added in the upgrade pass.
TickerData = pc.TickerData

# ----------------------------
# Indicator Logic
# ----------------------------
def find_troughs(values: List[float], min_separation: int = 3) -> List[int]:
    """Local trough finder (mirror of find_peaks for bullish divergence)."""
    troughs: List[int] = []
    n = len(values)
    if n < 3:
        return troughs
    for i in range(1, n - 1):
        if values[i] < values[i - 1] and values[i] < values[i + 1]:
            if not troughs or (i - troughs[-1] >= min_separation):
                troughs.append(i)
    return troughs

def detect_bullish_divergence(closes: List[float], rsi_values: List[Optional[float]]) -> bool:
    """
    Bullish divergence: price makes a lower low while RSI makes a higher low.
    This signals exhaustion of sellers and a likely reversal upward.
    """
    if len(closes) < DIVERGENCE_WINDOW or len(rsi_values) < DIVERGENCE_WINDOW:
        return False
    price_window = pc.np.asarray(closes[-DIVERGENCE_WINDOW:], dtype=float)
    rsi_window_list = rsi_values[-DIVERGENCE_WINDOW:]
    if any(v is None for v in rsi_window_list):
        return False
    rsi_window = pc.np.asarray([float(v) for v in rsi_window_list], dtype=float)

    price_troughs = find_troughs(price_window.tolist())
    rsi_troughs = find_troughs(rsi_window.tolist())

    if len(price_troughs) < 2 or len(rsi_troughs) < 2:
        return False

    # Time-alignment check — ensure price and RSI troughs occur within 5 candles of each other
    if abs(price_troughs[-1] - rsi_troughs[-1]) > 5 or abs(price_troughs[-2] - rsi_troughs[-2]) > 5:
        return False

    p1 = price_window[price_troughs[-2]]
    p2 = price_window[price_troughs[-1]]
    r1 = rsi_window[rsi_troughs[-2]]
    r2 = rsi_window[rsi_troughs[-1]]

    # p2 meaningfully lower, r2 meaningfully higher → bullish divergence
    # Apply stricter thresholds and ensure second RSI trough is in oversold zone
    return (p2 < p1 * DIV_PRICE_THRESHOLD) and (r2 > r1 + DIV_RSI_THRESHOLD) and (r2 < RSI_OVERSOLD_ZONE + 10)

def detect_patterns(ohlc: List[Tuple[float, float, float, float]]) -> List:
    """Detect bullish reversal / continuation candle patterns."""
    patterns = []
    if len(ohlc) < 3:
        return patterns

    def body(c): return abs(c[3] - c[0])
    def upper_wick(c): return c[1] - max(c[0], c[3])
    def lower_wick(c): return min(c[0], c[3]) - c[2]
    def is_bear(c): return c[3] < c[0]
    def is_bull(c): return c[3] > c[0]

    c0, c1, c2 = ohlc[-3], ohlc[-2], ohlc[-1]

    # Hammer — long lower wick, small body, at low area (bullish reversal)
    if (lower_wick(c2) > 2 * body(c2)
            and upper_wick(c2) < body(c2) * 0.4
            and body(c2) > 0):
        patterns.append(("Hammer 🔨", 15, 1.0))

    # Inverted Hammer / Dragonfly Doji at low (demand spike)
    if (lower_wick(c2) > 2.5 * body(c2)
            and body(c2) < (c2[1] - c2[2]) * 0.2
            and c2[2] < c1[2]):
        patterns.append(("Dragonfly Doji / Inv Hammer 🐉", 14, 1.0))

    # Bullish Engulfing — bear candle followed by larger bull candle
    if (is_bear(c1) and is_bull(c2)
            and c2[0] <= c1[3] and c2[3] >= c1[0]
            and body(c2) > body(c1)):
        patterns.append(("Bullish Engulfing 🟢", 18, 1.0))

    # Morning Star — bear, small body (indecision), bull (3-candle reversal)
    if (is_bear(c0)
            and body(c1) < body(c0) * 0.5
            and is_bull(c2)
            and c2[3] > (c0[0] + c0[3]) / 2):
        patterns.append(("Morning Star ⭐", 20, 1.0))

    # Piercing Line — bear candle, bull opens below low, closes above midpoint
    if (is_bear(c1) and is_bull(c2)
            and c2[0] < c1[2]
            and c2[3] > (c1[0] + c1[3]) / 2
            and c2[3] < c1[0]):
        patterns.append(("Piercing Line 💉", 16, 1.0))

    # Bullish Harami — bear followed by smaller bull inside it
    if (is_bear(c1) and is_bull(c2)
            and c2[0] > c1[3] and c2[3] < c1[0]
            and body(c2) < body(c1)):
        patterns.append(("Bullish Harami 🟩", 12, 1.0))

    # Doji at Low — indecision after downtrend (reversal warning)
    if body(c2) < (c2[1] - c2[2]) * 0.15 and c2[2] < c1[2]:
        patterns.append(("Doji at Low — Reversal Watch 🔄", 10, 1.0))

    # Three White Soldiers (gate applied later — RSI must not be overbought)
    if (is_bull(c0) and is_bull(c1) and is_bull(c2)
            and c1[3] > c0[3] and c2[3] > c1[3]
            and body(c0) > 0 and body(c1) > 0 and body(c2) > 0):
        patterns.append(("Three White Soldiers 🪖", 18, 1.0))

    # Bullish Marubozu — strong bull with almost no wicks (momentum)
    if (is_bull(c2)
            and upper_wick(c2) < body(c2) * 0.1
            and lower_wick(c2) < body(c2) * 0.1
            and body(c2) > (c2[1] - c2[2]) * 0.85):
        patterns.append(("Bullish Marubozu 💪", 14, 1.0))

    return patterns

# ----------------------------
# Confidence & Scoring — LONG BIASED
# ----------------------------
def calc_confidence(rsi, bb_pct, ema21, price, change_24h, funding_rate, patterns, score, dist_low_pct, vol_spike):
    """
    Long-biased confidence: counts bullish agreeing signals vs bearish conflicts.
    """
    agreeing = 0.0
    conflicts = 0.0
    notes: List[str] = []

    # RSI — oversold is bullish, overbought is conflict for longs
    if rsi is not None:
        if rsi < 45.0:
            agreeing += 1.0
        elif rsi > 65.0:
            conflicts += 1.0
            notes.append("RSI overbought — late entry risk")

    # BB position — at/below lower is bullish, above upper is conflict
    if bb_pct is not None:
        if bb_pct <= 35.0:
            agreeing += 1.0
        elif bb_pct > 70.0:
            conflicts += 1.0
            notes.append("price above BB 70%")

    # EMA distance — below EMA is mean-reversion fuel
    if ema21 is not None and price is not None:
        pct = pc.pct_change(price, ema21)
        if pct < -1.0:
            agreeing += 1.0        # price meaningfully below EMA21
        elif pct > 2.0:
            conflicts += 0.5       # already stretched above EMA21

    # 24h change — a significant drop is oversold fuel; extreme dump is risky
    if change_24h is not None:
        if -15.0 <= change_24h <= -3.0:
            agreeing += 1.0
        elif change_24h < -15.0:
            agreeing += 0.5        # capitulation possible but risky
        elif change_24h > 5.0:
            conflicts += 1.0
            notes.append("pumping already")
        elif -0.5 < change_24h < 0.5:
            conflicts += 0.5
            notes.append("flat — no momentum")

    # Funding — negative funding is bullish (shorts crowded = squeeze)
    if funding_rate is not None:
        fr_pct = funding_rate * 100.0
        if fr_pct < -0.01:
            agreeing += 1.0
        elif fr_pct > 0.05:
            conflicts += 2.0
            notes.append("crowded longs")

    # Near 24h low
    if dist_low_pct is not None and dist_low_pct < 1.0:
        agreeing += 1.0

    # Volume spike (capitulation buying or short squeeze)
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
        data.rsi, bb_pct, data.ema21, data.price,
        data.change_24h, data.funding_rate, data.patterns,
        score, data.dist_low_pct, data.vol_spike,
    )


def score_long(data: TickerData) -> Tuple[int, List[str]]:
    """
    Aggregate a score for a LONG setup.
    All signals are long-biased (inverse of the short scanner).
    """
    score = 0
    signals: List[str] = []

    # ── Regime Multipliers (Adaptive Geometry) ──
    # Trending regime favors momentum; Ranging regime favors mean-reversion
    m_trend = 1.2 if data.regime == "TRENDING" else 1.0
    m_rev   = 1.2 if data.regime == "RANGING" else 1.0

    if data.regime != "UNKNOWN":
        signals.append(f"Market Regime: {data.regime} — Adjusting weights (x{m_trend if m_trend > 1 else m_rev})")

    # --- EMA slope ---
    if data.ema_slope is not None:
        if data.ema_slope > 0.0:
            score += int(12 * m_trend)
            signals.append(f"Positive EMA Slope ({data.ema_slope:.3f}) — Uptrend confirmed")
        elif data.slope_change is not None and data.slope_change > 0.01:
            score += int(8 * m_trend)
            signals.append(f"EMA Curling Up (Slope Δ +{data.slope_change:.3f}) — Momentum building")
        elif data.ema_slope < 0.0 and data.slope_change is not None and data.slope_change > 0.02:
            score += int(5 * m_trend)
            signals.append(f"EMA Slope Flattening (Δ +{data.slope_change:.3f}) — Downtrend slowing")

    # --- News ---
    if data.news_count > 0:
        signals.append(f"NEWS: {data.news_count} recent items (Proceed with caution)")

    # --- Volume profile support node ---
    if data.dist_to_node_below is not None:
        if data.dist_to_node_below < 0.5:
            score += 15
            signals.append(f"Near High-Vol Support Node ({data.dist_to_node_below:.2f}% above)")
        elif data.dist_to_node_below < 1.0:
            score += 8
            signals.append(f"Approaching Support Node ({data.dist_to_node_below:.2f}% above)")

    # --- Spread / liquidity ---
    if data.spread is not None:
        if data.spread > 0.15:
            score -= 10
            signals.append(f"Low Liquidity (Spread {data.spread:.2f}%)")
        elif data.spread < 0.05:
            score += 5
            signals.append(f"High Liquidity (Spread {data.spread:.2f}%)")

    # --- Funding momentum (becoming more negative = more squeeze fuel) ---
    if data.fr_change is not None and data.fr_change < 0.0:
        score += int(WEIGHTS["funding_momentum"] * m_trend)
        signals.append(f"Funding Momentum (becoming more negative {data.fr_change*100:.4f}% — squeeze building)")

    # --- 1H RSI alignment ---
    if data.rsi_1h is not None:
        if data.rsi_1h < 35.0:
            score += WEIGHTS["htf_align_oversold"]
            signals.append(f"HTF Alignment (1H RSI {data.rsi_1h:.1f}) — deeply oversold")
        elif data.rsi_1h < 45.0:
            score += 8
            signals.append(f"HTF Alignment (1H RSI {data.rsi_1h:.1f}) — oversold territory")

    # --- Bullish divergence ---
    if data.has_div:
        score += int(WEIGHTS["divergence"] * m_rev)
        signals.append("Bullish Divergence (Price LL vs RSI HL) — sellers exhausted")

    # --- RSI scoring bands ---
    if data.rsi is not None:
        recovering = (data.prev_rsi is not None) and (data.rsi > data.prev_rsi)

        if data.rsi < 25.0:
            score += int(WEIGHTS["rsi_oversold"] * m_rev)
            signals.append(f"RSI {data.rsi:.1f} (extremely oversold — high-risk/high-reward)")
        elif 25.0 <= data.rsi <= 45.0:
            pts = int(WEIGHTS["rsi_recovery"] * m_rev)
            label = f"RSI {data.rsi:.1f} (oversold recovery zone)"
            if recovering:
                pts += 8
                label += " ✓ turning up"
            score += pts
            signals.append(label)
        elif 55.0 < data.rsi <= 65.0:
            score += 2
            signals.append(f"RSI {data.rsi:.1f} (mildly elevated)")
        elif data.rsi > 65.0:
            score -= 5
            signals.append(f"RSI {data.rsi:.1f} (overbought — risky long entry)")

    # --- Bollinger Band position ---
    if data.bb is not None:
        bb_range = data.bb["upper"] - data.bb["lower"]
        bb_pct = ((data.price - data.bb["lower"]) / bb_range) if bb_range > 0.0 else 0.5

        if bb_pct <= 0.10:
            score += int(WEIGHTS["bb_lower_90"] * m_rev)
            signals.append(f"Price below/at BB lower band ({bb_pct*100:.0f}%) — extreme oversold")
        elif bb_pct <= 0.25:
            score += int(WEIGHTS["bb_lower_75"] * m_rev)
            signals.append(f"Near BB lower band ({bb_pct*100:.0f}%) — oversold")
        elif bb_pct <= 0.45:
            score += 5  # Fixed to +5
            signals.append(f"Below BB mid ({bb_pct*100:.0f}%)")
        elif bb_pct <= 0.55:
            score += 0  # Fixed to 0
            signals.append(f"At BB mid ({bb_pct*100:.0f}%)")
        else:
            score -= 5
            signals.append(f"Above BB mid — fading long ({bb_pct*100:.0f}%)")

    # --- EMA21 distance (mean reversion) ---
    if data.ema21 is not None and data.price is not None:
        pct_from_ema = pc.pct_change(data.price, data.ema21)
        if pct_from_ema < -3.0:
            score += int(WEIGHTS["ema_stretch_3"] * m_rev)
            signals.append(f"Price {abs(pct_from_ema):.1f}% below EMA21 (mean-reversion opportunity)")
            if data.rsi and data.rsi < 35.0:
                score += 5
                signals.append("Stretch bonus: Deeply oversold RSI + Below EMA21")
        elif pct_from_ema < -1.0:
            score += 5
            signals.append(f"Price {abs(pct_from_ema):.1f}% below EMA21")
        elif pct_from_ema > 1.0:
            score -= 10
            signals.append(f"Price {pct_from_ema:.1f}% above EMA21 (extended)")

    # --- 24h change scoring ---
    if data.change_24h is not None:
        if 3.0 <= data.change_24h <= 10.0:
            score += int(12 * m_trend)
            signals.append(f"+{data.change_24h:.1f}% (bullish momentum)")
        elif data.change_24h > 10.0:
            score += 0
            signals.append(f"+{data.change_24h:.1f}% (overextended)")
        elif data.change_24h < -12.0:
            score += 20
            signals.append(f"{data.change_24h:.1f}% crash (capitulation)")
        elif -12.0 <= data.change_24h <= -5.0:
            score += 12
            signals.append(f"{data.change_24h:.1f}% dip (oversold bounce)")
        elif -5.0 < data.change_24h < -2.0:
            score += 5  # Fixed to +5
            signals.append(f"{data.change_24h:.1f}% pullback (controlled dip buy)")
        else:
            signals.append(f"{data.change_24h:+.1f}% (neutral)")

    # --- Distance from 24h LOW ---
    if data.dist_low_pct is not None:
        if data.dist_low_pct < 1.0:
            score += 12
            signals.append(f"Near 24h Low ({data.dist_low_pct:.1f}% distance) — demand zone")
        elif data.dist_low_pct < 2.0:
            score += 6
            signals.append(f"Close to 24h Low ({data.dist_low_pct:.1f}% distance)")

    # --- Volume spike (capitulation or institutional accumulation) ---
    if data.vol_spike > 2.0:
        score += int(WEIGHTS["vol_spike_2"] * m_trend)
        signals.append(f"Volume spike ({data.vol_spike:.1f}x average) — capitulation / accumulation")
    elif data.vol_spike > 1.4:
        score += 7
        signals.append(f"Elevated volume ({data.vol_spike:.1f}x average)")

    # --- Funding rate scoring ---
    if data.funding_rate is not None:
        fr_pct = data.funding_rate * 100.0
        if fr_pct < -0.10:
            score += int(WEIGHTS["funding_negative"] * m_rev)
            signals.append(f"Funding {fr_pct:.4f}% (heavily crowded shorts — squeeze primed)")
        elif fr_pct < -0.05:
            score += 16
            signals.append(f"Funding {fr_pct:.4f}% (crowded shorts)")
        elif fr_pct < -0.01:
            score += 8
            signals.append(f"Funding {fr_pct:.4f}% (mild short bias)")
        elif fr_pct > 0.05:
            score -= 12
            signals.append(f"Funding +{fr_pct:.4f}% (crowded longs — risky entry)")

    # --- Candle patterns ---
    for name, bonus, quality in data.patterns:
        q = float(quality) if isinstance(quality, (int, float)) else 1.0
        weighted_bonus = int(bonus * q)
        score += weighted_bonus
        q_label = f" (x{q:.1f} Quality)" if abs(q - 1.0) > 1e-6 else ""
        signals.append(f"Pattern: {name} (+{weighted_bonus}{q_label})")

    # Return the raw score unclamped to accurately reflect multiple penalties
    return int(round(score)), signals


# ----------------------------
# Proxies
# ----------------------------
def get_tickers(rps: float = None) -> List[dict]:
    return pc.get_tickers(rps)

def get_candles(symbol: str, timeframe: str = "15m", limit: int = 100, rps: float = None) -> List[List[Any]]:
    return pc.get_candles(symbol, timeframe, limit, rps)

prefetch_all_funding_rates = pc.prefetch_all_funding_rates

# ----------------------------
# Main Analysis
# ----------------------------
def analyse(ticker: dict, cfg: dict, enable_ai: bool = True, enable_entity: bool = True,
            scan_id: str = None) -> dict | None:
    """
    Analyse a single Phemex USDT-M perpetual ticker for LONG setups.

    [T1-02] Delegates to pc.unified_analyse(), which is the single source of
    truth for signal generation.  All upgrade logic (slippage model Upgrade #1,
    volatility filter Upgrade #6, order book imbalance Upgrade #10) is now
    active on every scan call rather than being silently orphaned in a function
    that was never called from production code.

    Direction-specific behaviour is injected via callbacks:
      score_func           → score_long
      detect_patterns_func → detect_patterns (bullish patterns)
      detect_div_func      → detect_bullish_divergence
      calc_confidence_func → _calc_confidence_adapter (wraps calc_confidence)

    A pre-score gate (default 60) is applied inside unified_analyse after the
    cheap indicator pass; symbols below the threshold skip the expensive
    order-book / HTF-candle / volume-profile API calls.
    """
    return pc.unified_analyse(
        ticker=ticker,
        cfg=cfg,
        direction="LONG",
        score_func=score_long,
        detect_patterns_func=detect_patterns,
        detect_div_func=detect_bullish_divergence,
        calc_confidence_func=_calc_confidence_adapter,
        enable_ai=enable_ai,
        enable_entity=enable_entity,
        scan_id=scan_id,
    )
