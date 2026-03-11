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
FancyFangBot Backtester v2.0
==========================
Walk-forward backtester that replays scanner signals on real historical candle data.

How it works:
  1. Fetches N candles per symbol from Phemex (same endpoints the scanner uses)
  2. Slides a 100-candle window forward one candle at a time
  3. At each step, scores the window using the EXACT same logic as phemex_long/short.py
  4. When score >= threshold: enters at NEXT candle OPEN (no lookahead bias)
  5. Slippage = spread/2 on entry + spread/2 on exit (market order crosses half the spread)
  6. Trailing stop tracked on candle HIGH/LOW (not close) — stops hit realistically
  7. Hard stop loss fires before trailing stop if price blows through (optional)
  8. Records every trade with full signal breakdown

Modes:
  python backtest.py              -- single run with defaults
  python backtest.py --sweep      -- grid search over trail%, score, leverage

Usage examples:
  python backtest.py --timeframe 15m --candles 500 --min-score 100
  python backtest.py --symbols BTCUSDT ETHUSDT SOLUSDT --trail-pct 0.008
  python backtest.py --stop-loss-pct 0.03 --cooldown 5 --direction LONG
  python backtest.py --sweep --sweep-symbols 30
  python backtest.py --csv  -- also saves trades to backtest_results.csv
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import argparse
import concurrent.futures
import csv
import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
import core.phemex_common as pc
from core.phemex_long import (
    score_long,
    feature_builder_long,
    detect_patterns as detect_patterns_long,
    detect_bullish_divergence
)
from core.phemex_short import (
    score_short,
    feature_builder_short,
    detect_patterns as detect_patterns_short,
    detect_bearish_divergence
)
import core.ui as ui
from modules.banner import BANNER
from colorama import init, Fore, Style
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()
init(autoreset=True)

BASE_URL = os.getenv("PHEMEX_BASE_URL", "https://api.phemex.com").replace(
    "testnet-api.phemex.com", "api.phemex.com"  # always use mainnet for market data
)

TIMEFRAME_MAP = {
    "1m":  60,    "3m":  180,   "5m":  300,   "15m": 900,
    "30m": 1800,  "1H":  3600,  "2H":  7200,  "4H":  14400,
    "6H":  21600, "12H": 43200, "1D":  86400,
}

# Candles per year per timeframe — used for Sharpe/Sortino annualisation
CANDLES_PER_YEAR = {
    "1m": 525_600, "3m": 175_200, "5m": 105_120, "15m": 35_040,
    "30m": 17_520,  "1H": 8_760,   "2H": 4_380,   "4H": 2_190,
    "6H": 1_460,    "12H": 730,    "1D": 365,
}

TAKER_FEE = 0.0006  # 0.06% per side (Phemex USDT-M maker/taker)
MAX_MARGIN_PER_SYMBOL = 150.0

def pick_sim_leverage(atr_stop_pct: float | None, vol_spike: float = 1.0, is_low_liq: bool = False) -> int:
    """Select leverage based on asset volatility measured at scan time. (Ported from core.sim_bot)"""
    if atr_stop_pct is None:
        return 30

    # vol spike modifier
    spike_adj = 5 if vol_spike >= 3.0 else (2 if vol_spike >= 2.0 else 0)
    effective_atr = atr_stop_pct + spike_adj

    if effective_atr >= 4.0:
        lev = 5
    elif effective_atr >= 2.5:
        lev = 10
    elif effective_atr >= 1.5:
        lev = 15
    elif effective_atr >= 0.8:
        lev = 20
    else:
        lev = 30

    if is_low_liq:
        return min(lev, 10)
    return lev

# ─────────────────────────────────────────────────────────────────────
# HTTP session
# ─────────────────────────────────────────────────────────────────────
_thread_local = threading.local()

def _get_session() -> requests.Session:
    """Provides a thread-local requests Session with retry logic."""
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50))
        _thread_local.session = session
    return _thread_local.session

def _get(url: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    """Internal GET helper with error handling and response parsing."""
    try:
        response = _get_session().get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────
# Market data fetchers
# ─────────────────────────────────────────────────────────────────────
def get_tickers(min_vol: float = 1_000_000) -> List[dict]:
    """Fetches all 24h tickers from Phemex and filters by USDT and minimum turnover."""
    data = _get(f"{BASE_URL}/md/v3/ticker/24hr/all")
    if not data or data.get("error"):
        return []
    result = data.get("result") or data.get("data") or {}
    tickers = result.get("tickers", []) if isinstance(result, dict) else result
    if not tickers:
        tickers = data if isinstance(data, list) else []
    return [
        ticker for ticker in tickers
        if str(ticker.get("symbol", "")).endswith("USDT")
        and float(ticker.get("turnoverRv") or 0) >= min_vol
    ]

def get_candles(symbol: str, timeframe: str = "15m", limit: int = 500) -> List[list]:
    """Fetch OHLCV using common helper which handles limit mapping."""
    return pc.get_candles(symbol, timeframe, limit)

def get_spread_pct(symbol: str) -> Optional[float]:
    """Calculates the best bid-ask spread as a percentage of the mid price."""
    data = _get(f"{BASE_URL}/md/v2/orderbook", params={"symbol": symbol})
    if not data or data.get("error"):
        return None
    try:
        book = (data.get("result") or {}).get("orderbook_p") or {}
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        if best_bid <= 0:
            return None
        return (best_ask - best_bid) / best_bid * 100.0
    except Exception:
        return None

def get_funding(symbol: str) -> Optional[float]:
    """Fetches the current real funding rate for a given symbol."""
    data = _get(
        f"{BASE_URL}/contract-biz/public/real-funding-rates",
        params={"symbol": symbol},
    )
    if not data:
        return None
    try:
        items = data if isinstance(data, list) else data.get("data", [])
        if not items:
            return None
        entry = next((item for item in items if item.get("symbol") == symbol), items[0])
        return float(entry.get("fundingRate", 0.0))
    except Exception:
        return None

def get_htf_rsi(symbol: str, tf: str = "1H") -> Optional[float]:
    """Fetches High Time Frame (HTF) RSI for trend alignment."""
    rows = get_candles(symbol, timeframe=tf, limit=50)
    if not rows:
        return None
    closes = []
    for row in rows:
        try:
            closes.append(float(row[6]))
        except Exception:
            continue
    if len(closes) < 16:
        return None
    rsi, _, _ = pc.calc_rsi(closes)
    return rsi


def score_window_unified(
    symbol: str,
    window_data: List[Tuple[float, float, float, float, float]],
    direction: str,
    rsi_1h: Optional[float] = None,
    funding: Optional[float] = None,
    spread: Optional[float] = None,
) -> Tuple[int, List[str]]:
    """Bridge between backtester window and production scoring logic."""
    closes = [x[3] for x in window_data]
    highs = [x[1] for x in window_data]
    lows = [x[2] for x in window_data]
    vols = [x[4] for x in window_data]
    ohlc_basic = [(x[0], x[1], x[2], x[3], x[4]) for x in window_data]

    last = closes[-1]
    rsi, prev_rsi, rsi_hist = pc.calc_rsi(closes)
    bb = pc.calc_bb(closes)
    ema_series = pc.calc_ema_series(closes, 21)
    ema21 = ema_series[-1] if ema_series else None
    ema_slope, slope_change = pc.calc_ema_slope(ema_series)
    vol_spike = pc.calc_volume_spike(vols)
    adx = pc.calc_adx(highs, lows, closes)
    poc_price, _ = pc.calc_volume_profile(window_data, vols)
    regime, entropy = pc.calc_market_regime(closes)
    kalman_series = pc.calc_kalman_series(closes)
    kalman_slope = kalman_series[-1] - kalman_series[-2] if len(kalman_series) >= 2 else 0.0

    # 24h stats approximation (from the 100-candle window)
    open_24h = closes[0]
    low_24h = min(lows)
    high_24h = max(highs)

    if direction == "LONG":
        patterns = detect_patterns_long(ohlc_basic)
        has_div = detect_bullish_divergence(closes, rsi_hist)
        score_func = score_long
    else:
        patterns = detect_patterns_short(ohlc_basic)
        has_div = detect_bearish_divergence(closes, rsi_hist)
        score_func = score_short

    data = pc.TickerData(
        inst_id=symbol, price=last, rsi=rsi, prev_rsi=prev_rsi, bb=bb, ema21=ema21,
        change_24h=pc.pct_change(last, open_24h), funding_rate=funding, patterns=patterns,
        dist_low_pct=pc.pct_change(last, low_24h), dist_high_pct=pc.pct_change(last, high_24h),
        vol_spike=vol_spike, has_div=has_div, rsi_1h=rsi_1h,
        fr_change=0.0, spread=spread,
        ema_slope=ema_slope, slope_change=slope_change,
        adx=adx, poc_price=poc_price,
        raw_ohlc=ohlc_basic[-10:],
        regime=regime, entropy=entropy, kalman_slope=kalman_slope,
    )

    return score_func(data)

# ─────────────────────────────────────────────────────────────────────
# Trade dataclass
# ─────────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    symbol:        str
    direction:     str           # "LONG" | "SHORT"
    entry_idx:     int           # index into ohlcv list
    entry_price:   float
    size:          float = 0.0
    exit_idx:      Optional[int] = None
    exit_price:    Optional[float] = None
    pnl_usdt:      Optional[float] = None
    pnl_pct:       Optional[float] = None   # raw price move %
    score:         int = 0
    signals:       List[str] = field(default_factory=list)
    exit_reason:   str = "open"  # "trail_stop" | "hard_stop" | "max_hold" | "end_of_data"
    hold_candles:  int = 0
    slippage_pct:  float = 0.0
    leverage:      int = 30
    margin:        float = 10.0
    trail_pct:     float = 0.005
    is_low_liq:    bool = False

# ─────────────────────────────────────────────────────────────────────
# Core walk-forward backtester for one symbol
# ─────────────────────────────────────────────────────────────────────
def _calculate_dynamic_cooldown_candles(pnl_usdt: float, base_win: int = 1, base_loss: int = 2, max_c: int = 8) -> int:
    """Calculates a dynamic cooldown in candles based on trade performance."""
    if pnl_usdt >= 0:
        return base_win
    
    # Scale loss penalty: e.g., for every $25 lost, add 1 candle of cooldown
    loss_penalty = int(abs(pnl_usdt) / 25.0)
    return min(base_loss + loss_penalty, max_c)

def backtest_symbol(
    symbol:            str,
    candles:           List[list],
    spread:            Optional[float],
    funding:           Optional[float],
    rsi_1h:            Optional[float],
    min_score:         int,
    trail_pct:         float,
    leverage:          int,
    margin:            float,
    max_margin:        float,
    window:            int   = 100,
    max_hold:          int   = 96,
    min_score_low_liq: int   = 145,
    hard_stop_pct:     float = 0.0,   # 0 = disabled; e.g. 0.03 = 3% hard stop from entry
    take_profit_pct:   float = 0.0,   # 0 = disabled; e.g. 0.05 = 5% take profit from entry
    cooldown:          int   = 0,     # min candles between trades (re-entry guard)
    direction:         str   = "BOTH",# "LONG" | "SHORT" | "BOTH"
    min_score_gap:     int   = 0,     # min gap between long and short scores to enter
    min_signals:       int   = 3,
) -> List[Trade]:
    """
    Simulates a walk-forward backtest for a single symbol.
    Iterates through historical candles, calculates scores, and manages simulated trades.
    """
    # Reset predictive engine normalizers for this symbol
    feature_builder_long.reset_normalizers()
    feature_builder_short.reset_normalizers()

    if len(candles) < window + 2:
        return []

    # Parse to OHLCV tuples
    ohlcv_data: List[Tuple[float, float, float, float, float]] = []
    for candle in candles:
        try:
            ohlcv_data.append((float(candle[3]), float(candle[4]), float(candle[5]), float(candle[6]),
                               float(candle[7]) if len(candle) > 7 else 0.0))
        except Exception:
            continue
    if len(ohlcv_data) < window + 2:
        return []

    is_low_liq  = spread is not None and spread > 0.15
    eff_min_score = min_score_low_liq if is_low_liq else min_score

    # Slippage: half the spread on each side (market order crosses half the spread)
    slip_one_side = (spread / 2.0 / 100.0) if spread is not None else 0.0008
    fee_one_side  = TAKER_FEE

    direction_upper = direction.upper()

    trades:       List[Trade] = []
    is_in_position = False
    active_position: Optional[Trade] = None
    high_water_mark = 0.0
    low_water_mark = float("inf")
    stop_price = 0.0
    last_exit_index = -(cooldown + 1)   # allows entry on very first bar
    current_cooldown = cooldown

    for candle_index in range(window, len(ohlcv_data) - 1):
        current_open, current_high, current_low, current_close, current_volume = ohlcv_data[candle_index]

        # ── Look for entry signal on this window ──────────────────────
        window_data = ohlcv_data[candle_index - window: candle_index]
        closes_window = [x[3] for x in window_data]
        highs_window  = [x[1] for x in window_data]
        lows_window   = [x[2] for x in window_data]
        vols_window   = [x[4] for x in window_data]

        long_score, long_signals = (0, []) if direction_upper == "SHORT" else \
            score_window_unified(symbol, window_data, "LONG", rsi_1h, funding, spread)
        short_score, short_signals = (0, []) if direction_upper == "LONG" else \
            score_window_unified(symbol, window_data, "SHORT", rsi_1h, funding, spread)

        best_score = max(long_score, short_score)
        
        # ── Handle existing position (Scale-in check) ──────────────────
        if is_in_position and active_position:
            # ── 1. Position Management (TP/SL/Trail) ───────────────────────
            if active_position.direction == "LONG":
                # Take profit check (using candle high)
                if take_profit_pct > 0:
                    tp_level = active_position.entry_price * (1.0 + take_profit_pct)
                    if current_high >= tp_level:
                        exit_price = tp_level * (1.0 - slip_one_side - fee_one_side)
                        raw_return = (exit_price - active_position.entry_price) / active_position.entry_price
                        active_position.exit_idx     = candle_index
                        active_position.exit_price   = exit_price
                        active_position.pnl_pct      = raw_return * 100
                        active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
                        active_position.exit_reason  = "take_profit"
                        active_position.hold_candles = candle_index - active_position.entry_idx
                        trades.append(active_position)
                        pnl_for_cooldown = active_position.pnl_usdt
                        is_in_position = False; active_position = None
                        last_exit_index = candle_index
                        current_cooldown = _calculate_dynamic_cooldown_candles(pnl_for_cooldown, base_win=1, base_loss=max(2, cooldown))
                        continue

                # Hard stop check (using candle low)
                if hard_stop_pct > 0:
                    hard_stop_level = active_position.entry_price * (1.0 - hard_stop_pct)
                    if current_low <= hard_stop_level:
                        exit_price = hard_stop_level * (1.0 - slip_one_side - fee_one_side)
                        raw_return = (exit_price - active_position.entry_price) / active_position.entry_price
                        active_position.exit_idx     = candle_index
                        active_position.exit_price   = exit_price
                        active_position.pnl_pct      = raw_return * 100
                        active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
                        active_position.exit_reason  = "hard_stop"
                        active_position.hold_candles = candle_index - active_position.entry_idx
                        trades.append(active_position)
                        pnl_for_cooldown = active_position.pnl_usdt
                        is_in_position = False; active_position = None
                        last_exit_index = candle_index
                        current_cooldown = _calculate_dynamic_cooldown_candles(pnl_for_cooldown, base_win=1, base_loss=max(2, cooldown))
                        continue

                # Trailing stop check (using candle low)
                if current_low <= stop_price:
                    exit_price = stop_price * (1.0 - slip_one_side - fee_one_side)
                    raw_return = (exit_price - active_position.entry_price) / active_position.entry_price
                    active_position.exit_idx     = candle_index
                    active_position.exit_price   = exit_price
                    active_position.pnl_pct      = raw_return * 100
                    active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
                    active_position.exit_reason  = "trail_stop"
                    active_position.hold_candles = candle_index - active_position.entry_idx
                    trades.append(active_position)
                    pnl_for_cooldown = active_position.pnl_usdt
                    is_in_position = False; active_position = None
                    last_exit_index = candle_index
                    current_cooldown = _calculate_dynamic_cooldown_candles(pnl_for_cooldown, base_win=1, base_loss=max(2, cooldown))
                    continue

                # Ratchet high-water on candle high
                if current_high > high_water_mark:
                    high_water_mark = current_high
                    stop_price = high_water_mark * (1.0 - trail_pct)

            else:  # SHORT
                # Take profit check (using candle low)
                if take_profit_pct > 0:
                    tp_level = active_position.entry_price * (1.0 - take_profit_pct)
                    if current_low <= tp_level:
                        exit_price = tp_level * (1.0 + slip_one_side + fee_one_side)
                        raw_return = (active_position.entry_price - exit_price) / active_position.entry_price
                        active_position.exit_idx     = candle_index
                        active_position.exit_price   = exit_price
                        active_position.pnl_pct      = raw_return * 100
                        active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
                        active_position.exit_reason  = "take_profit"
                        active_position.hold_candles = candle_index - active_position.entry_idx
                        trades.append(active_position)
                        pnl_for_cooldown = active_position.pnl_usdt
                        is_in_position = False; active_position = None
                        last_exit_index = candle_index
                        current_cooldown = _calculate_dynamic_cooldown_candles(pnl_for_cooldown, base_win=1, base_loss=max(2, cooldown))
                        continue

                # Hard stop check (using candle high)
                if hard_stop_pct > 0:
                    hard_stop_level = active_position.entry_price * (1.0 + hard_stop_pct)
                    if current_high >= hard_stop_level:
                        exit_price = hard_stop_level * (1.0 + slip_one_side + fee_one_side)
                        raw_return = (active_position.entry_price - exit_price) / active_position.entry_price
                        active_position.exit_idx     = candle_index
                        active_position.exit_price   = exit_price
                        active_position.pnl_pct      = raw_return * 100
                        active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
                        active_position.exit_reason  = "hard_stop"
                        active_position.hold_candles = candle_index - active_position.entry_idx
                        trades.append(active_position)
                        pnl_for_cooldown = active_position.pnl_usdt
                        is_in_position = False; active_position = None
                        last_exit_index = candle_index
                        current_cooldown = _calculate_dynamic_cooldown_candles(pnl_for_cooldown, base_win=1, base_loss=max(2, cooldown))
                        continue

                # Trailing stop hit (using candle high)
                if current_high >= stop_price:
                    exit_price = stop_price * (1.0 + slip_one_side + fee_one_side)
                    raw_return = (active_position.entry_price - exit_price) / active_position.entry_price
                    active_position.exit_idx     = candle_index
                    active_position.exit_price   = exit_price
                    active_position.pnl_pct      = raw_return * 100
                    active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
                    active_position.exit_reason  = "trail_stop"
                    active_position.hold_candles = candle_index - active_position.entry_idx
                    trades.append(active_position)
                    pnl_for_cooldown = active_position.pnl_usdt
                    is_in_position = False; active_position = None
                    last_exit_index = candle_index
                    current_cooldown = _calculate_dynamic_cooldown_candles(pnl_for_cooldown, base_win=1, base_loss=max(2, cooldown))
                    continue

                # Ratchet low-water on candle low
                if current_low < low_water_mark:
                    low_water_mark = current_low
                    stop_price = low_water_mark * (1.0 + trail_pct)

            # Max hold exit at candle close
            if is_in_position and active_position and (candle_index - active_position.entry_idx >= max_hold):
                exit_price = current_close
                if active_position.direction == "LONG":
                    raw_return = (exit_price - active_position.entry_price) / active_position.entry_price
                else:
                    raw_return = (active_position.entry_price - exit_price) / active_position.entry_price
                active_position.exit_idx     = candle_index
                active_position.exit_price   = exit_price
                active_position.pnl_pct      = raw_return * 100
                active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
                active_position.exit_reason  = "max_hold"
                active_position.hold_candles = candle_index - active_position.entry_idx
                trades.append(active_position)
                pnl_for_cooldown = active_position.pnl_usdt
                is_in_position = False; active_position = None
                last_exit_index = candle_index
                current_cooldown = _calculate_dynamic_cooldown_candles(pnl_for_cooldown, base_win=1, base_loss=max(2, cooldown))
                continue

            # ── 2. Scale-in check (only if still in position) ───────────
            if is_in_position and active_position and (best_score >= eff_min_score):
                # Same direction?
                new_direction = "LONG" if long_score >= short_score else "SHORT"
                if new_direction == active_position.direction:
                    if active_position.margin < max_margin:
                        # Scale in at NEXT candle open
                        next_open = ohlcv_data[candle_index + 1][0]
                        if next_open > 0:
                            # 1 unit entry
                            unit_margin = margin
                            # Compute dynamic leverage for this unit
                            atr_w = pc.calc_atr(highs_window, lows_window, closes_window, 14)
                            vol_s = pc.calc_volume_spike(vols_window)
                            unit_lev = pick_sim_leverage(atr_w / next_open * 100.0 if atr_w else None, vol_s, is_low_liq)
                            
                            unit_notional = unit_margin * unit_lev
                            if new_direction == "LONG":
                                unit_price = next_open * (1.0 + slip_one_side + fee_one_side)
                            else:
                                unit_price = next_open * (1.0 - slip_one_side - fee_one_side)
                            
                            unit_size = unit_notional / unit_price
                            
                            # Weighted average entry
                            old_notional = active_position.size * active_position.entry_price
                            new_total_size = active_position.size + unit_size
                            avg_entry = (old_notional + unit_notional) / new_total_size
                            
                            active_position.entry_price = avg_entry
                            active_position.size = new_total_size
                            active_position.margin += unit_margin
                            
                            # Reset watermarks and stops
                            atr_stop_dist = (atr_w * 1.5) if atr_w else (next_open * active_position.trail_pct)
                            if new_direction == "LONG":
                                stop_price = avg_entry - atr_stop_dist
                                high_water_mark = max(high_water_mark, avg_entry)
                            else:
                                stop_price = avg_entry + atr_stop_dist
                                low_water_mark = min(low_water_mark, avg_entry)
            continue


        # ── Cooldown guard ────────────────────────────────────────────
        if current_cooldown > 0 and (candle_index - last_exit_index) < current_cooldown:
            continue

        # ── Look for entry signal on this window ──────────────────────
        window_data = ohlcv_data[candle_index - window: candle_index]
        closes_window = [x[3] for x in window_data]
        highs_window  = [x[1] for x in window_data]
        lows_window   = [x[2] for x in window_data]
        vols_window   = [x[4] for x in window_data]

        long_score, long_signals = (0, []) if direction_upper == "SHORT" else \
            score_window_unified(symbol, window_data, "LONG", rsi_1h, funding, spread)
        short_score, short_signals = (0, []) if direction_upper == "LONG" else \
            score_window_unified(symbol, window_data, "SHORT", rsi_1h, funding, spread)

        best_score = max(long_score, short_score)
        if best_score < eff_min_score:
            continue

        # Score gap filter — skip ambiguous signals
        if min_score_gap > 0 and abs(long_score - short_score) < min_score_gap:
            continue

        if long_score >= short_score:
            if len(long_signals) < min_signals:
                continue
            direction_trade, entry_score, entry_signals = "LONG",  long_score, long_signals
        else:
            if len(short_signals) < min_signals:
                continue
            direction_trade, entry_score, entry_signals = "SHORT", short_score, short_signals

        # ── Upgrade #3: Spread filter ─────────────────────────────────────────
        if spread is not None and spread > 0.40:   # Synchronized with pc.SPREAD_FILTER_MAX_PCT
            continue  # skip — too illiquid

        # ── Upgrade #6: Volatility filter on ATR / price ─────────────────────
        # Compute ATR on the current window for filtering and stop sizing
        atr_window = pc.calc_atr(highs_window, lows_window, closes_window, 14)
        mid_price = current_close
        if atr_window and mid_price > 0:
            vol_ratio = atr_window / mid_price
            if vol_ratio < 0.002:   # ATR < 0.2% of price — skip choppy market
                continue

        # Enter at NEXT candle OPEN (no lookahead bias)
        next_open = ohlcv_data[candle_index + 1][0]
        if next_open <= 0:
            continue

        # ── Dynamic Leverage (Ported from core.sim_bot) ───────────────────────────
        vol_s = pc.calc_volume_spike(vols_window)
        active_leverage = pick_sim_leverage(atr_window / next_open * 100.0 if atr_window else None, vol_s, is_low_liq)

        # ── Upgrade #2: ATR-based stop-loss (with fallback to trail_pct) ──────
        if atr_window and atr_window > 0:
            atr_stop_mult  = 1.5
            atr_stop_dist  = atr_window * atr_stop_mult
        else:
            atr_stop_dist  = next_open * trail_pct

        if direction_trade == "LONG":
            entry_price   = next_open * (1.0 + slip_one_side + fee_one_side)
            stop_price    = entry_price - atr_stop_dist   # ATR-based initial stop
            high_water_mark = entry_price
        else:
            entry_price  = next_open * (1.0 - slip_one_side - fee_one_side)
            stop_price   = entry_price + atr_stop_dist    # ATR-based initial stop
            low_water_mark  = entry_price

        active_position = Trade(
            symbol=symbol, direction=direction_trade,
            entry_idx=candle_index + 1, entry_price=entry_price,
            score=entry_score, signals=entry_signals,
            slippage_pct=(slip_one_side + fee_one_side) * 100,
            leverage=active_leverage, margin=margin, trail_pct=trail_pct,
            is_low_liq=is_low_liq,
        )
        is_in_position = True

    # Close any remaining position at last candle close
    if is_in_position and active_position is not None and candle_index > active_position.entry_idx:
        exit_price = ohlcv_data[-1][3]
        if active_position.direction == "LONG":
            raw_return = (exit_price - active_position.entry_price) / active_position.entry_price
        else:
            raw_return = (active_position.entry_price - exit_price) / active_position.entry_price
        active_position.exit_idx     = len(ohlcv_data) - 1
        active_position.exit_price   = exit_price
        active_position.pnl_pct      = raw_return * 100
        active_position.pnl_usdt     = raw_return * active_position.leverage * active_position.margin
        active_position.exit_reason  = "end_of_data"
        active_position.hold_candles = len(ohlcv_data) - 1 - active_position.entry_idx
        trades.append(active_position)

    return trades

# ─────────────────────────────────────────────────────────────────────
# Risk metrics
# ─────────────────────────────────────────────────────────────────────
def compute_drawdown(trades: List[Trade]) -> Tuple[float, float]:
    """Calculates the maximum drawdown in absolute USDT and percentage terms."""
    if not trades:
        return 0.0, 0.0
    current_equity = 0.0
    peak_equity = 0.0
    maximum_drawdown = 0.0
    for trade in trades:
        current_equity += trade.pnl_usdt or 0.0
        if current_equity > peak_equity:
            peak_equity = current_equity
        drawdown = peak_equity - current_equity
        if drawdown > maximum_drawdown:
            maximum_drawdown = drawdown
    max_drawdown_pct = (maximum_drawdown / peak_equity * 100.0) if peak_equity > 0 else 0.0
    return maximum_drawdown, max_drawdown_pct


def compute_sharpe(trades: List[Trade], timeframe: str = "15m") -> float:
    """Calculates the annualised Sharpe ratio (assuming a risk-free rate of 0)."""
    if len(trades) < 2:
        return np.nan
    pnl_array = np.array([trade.pnl_usdt or 0.0 for trade in trades], dtype=float)
    avg_hold_period = float(np.mean([trade.hold_candles for trade in trades])) or 1.0
    candles_per_year = CANDLES_PER_YEAR.get(timeframe, 35_040)
    trades_per_year = candles_per_year / avg_hold_period
    mean_return = float(np.mean(pnl_array))
    std_deviation = float(np.std(pnl_array, ddof=1))
    if std_deviation == 0:
        return np.nan
    return float(mean_return / std_deviation * math.sqrt(trades_per_year))


def compute_sortino(trades: List[Trade], timeframe: str = "15m") -> float:
    """Calculates the annualised Sortino ratio, focusing on downside risk."""
    if len(trades) < 2:
        return np.nan
    pnl_array = np.array([trade.pnl_usdt or 0.0 for trade in trades], dtype=float)
    avg_hold_period = float(np.mean([trade.hold_candles for trade in trades])) or 1.0
    candles_per_year = CANDLES_PER_YEAR.get(timeframe, 35_040)
    trades_per_year = candles_per_year / avg_hold_period
    mean_return = float(np.mean(pnl_array))
    negative_pnls = pnl_array[pnl_array < 0]
    if len(negative_pnls) < 2:
        return np.nan
    downside_deviation = float(np.std(negative_pnls, ddof=1))
    if downside_deviation == 0:
        return np.nan
    return float(mean_return / downside_deviation * math.sqrt(trades_per_year))


def fmt_stat(val: float, fmt: str = ".2f") -> str:
    """Formats a float, handling NaN by returning 'N/A'."""
    if np.isnan(val):
        return "N/A"
    return format(val, fmt)


def max_streaks(trades: List[Trade]) -> Tuple[int, int]:
    """Returns the maximum winning and losing streaks from a list of trades."""
    max_win_streak = max_loss_streak = current_win_streak = current_loss_streak = 0
    for trade in trades:
        if (trade.pnl_usdt or 0.0) > 0:
            current_win_streak += 1
            current_loss_streak = 0
        else:
            current_loss_streak += 1
            current_win_streak = 0
        max_win_streak = max(max_win_streak, current_win_streak)
        max_loss_streak = max(max_loss_streak, current_loss_streak)
    return max_win_streak, max_loss_streak

# ─────────────────────────────────────────────────────────────────────
# Parameter sweep
# ─────────────────────────────────────────────────────────────────────
@dataclass
class SweepResult:
    trail_pct:      float
    stop_loss_pct:  float
    take_profit_pct: float
    min_score:      int
    leverage:       int
    total_trades:   int
    wins:           int
    losses:         int
    win_rate:       float
    total_pnl:      float
    avg_win:        float
    avg_loss:       float
    profit_factor:  float
    avg_hold:       float
    expectancy:     float
    max_drawdown:   float = 0.0


def sweep(
    symbol_data_list: List[Tuple],
    trail_percentages: List[float],
    stop_loss_percentages: List[float],
    take_profit_percentages: List[float],
    min_scores: List[int],
    leverages: List[int],
    margin: float = 10.0,
    max_margin: float = 150.0,
    max_hold: int = 96,
    cooldown: int = 0,
    direction: str = "BOTH",
    min_score_gap: int = 0,
    min_signals: int = 3,
    window: int = 100,
) -> List[SweepResult]:
    """Runs a grid search over multiple parameter combinations to find optimal settings."""
    grid_combinations = [
        (tp_trail, sl, tp, ms, lv)
        for tp_trail in trail_percentages
        for sl in stop_loss_percentages
        for tp in take_profit_percentages
        for ms in min_scores
        for lv in leverages
    ]
    results = []
    for index, (tp_trail, stop_loss_pct, take_profit_pct, min_score, leverage) in enumerate(grid_combinations):
        all_trades = []
        for symbol, candles, spread, funding, rsi_1h in symbol_data_list:
            all_trades.extend(backtest_symbol(
                symbol, candles, spread, funding, rsi_1h,
                min_score=min_score, trail_pct=tp_trail, leverage=leverage,
                margin=margin, max_margin=max_margin, max_hold=max_hold,
                hard_stop_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
                cooldown=cooldown,
                direction=direction, min_score_gap=min_score_gap,
                min_signals=min_signals,
                window=window,
            ))
        closed_trades = [trade for trade in all_trades if trade.pnl_usdt is not None and trade.exit_reason != "open"]
        print(f"\r  Sweeping {index+1}/{len(grid_combinations)} — trail={tp_trail*100:.1f}% sl={stop_loss_pct*100:.1f}% tp={take_profit_pct*100:.1f}% score={min_score} lev={leverage}x"
              f" → {len(closed_trades)} trades", end="", flush=True)
        if not closed_trades:
            continue
        winning_trades = [trade for trade in closed_trades if trade.pnl_usdt > 0]
        losing_trades  = [trade for trade in closed_trades if trade.pnl_usdt <= 0]
        total_pnl = sum(trade.pnl_usdt for trade in closed_trades)
        gross_profit = sum(trade.pnl_usdt for trade in winning_trades)
        gross_loss = abs(sum(trade.pnl_usdt for trade in losing_trades))
        max_drawdown, _ = compute_drawdown(closed_trades)
        results.append(SweepResult(
            trail_pct=tp_trail, stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
            min_score=min_score, leverage=leverage,
            total_trades=len(closed_trades),
            wins=len(winning_trades), losses=len(losing_trades),
            win_rate=len(winning_trades) / len(closed_trades) * 100,
            total_pnl=total_pnl,
            avg_win=float(np.mean([trade.pnl_usdt for trade in winning_trades])) if winning_trades else 0,
            avg_loss=float(np.mean([trade.pnl_usdt for trade in losing_trades])) if losing_trades else 0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            avg_hold=float(np.mean([trade.hold_candles for trade in closed_trades])),
            expectancy=total_pnl / len(closed_trades),
            max_drawdown=max_drawdown,
        ))
    print()
    return sorted(results, key=lambda res: res.expectancy, reverse=True)

# ─────────────────────────────────────────────────────────────────────
# Stats & reporting
# ─────────────────────────────────────────────────────────────────────
def draw_bar(percentage: float, width: int = 20) -> str:
    """Returns an ASCII bar representing a percentage."""
    filled_length = int(percentage / 100 * width)
    return "█" * filled_length + "░" * (width - filled_length)


def print_stats(trades: List[Trade], label: str = "", timeframe: str = "15m"):
    """Prints a comprehensive statistical report for a list of trades."""
    closed_trades = [trade for trade in trades if trade.pnl_usdt is not None and trade.exit_reason != "open"]
    if not closed_trades:
        print(Fore.YELLOW + "  No closed trades to analyse."); return

    winning_trades = [trade for trade in closed_trades if trade.pnl_usdt > 0]
    losing_trades  = [trade for trade in closed_trades if trade.pnl_usdt <= 0]
    total_pnl = sum(trade.pnl_usdt for trade in closed_trades)
    win_rate  = len(winning_trades) / len(closed_trades) * 100
    avg_win   = float(np.mean([trade.pnl_usdt for trade in winning_trades])) if winning_trades else 0
    avg_loss  = float(np.mean([trade.pnl_usdt for trade in losing_trades])) if losing_trades else 0
    gross_profit = sum(trade.pnl_usdt for trade in winning_trades)
    gross_loss = abs(sum(trade.pnl_usdt for trade in losing_trades))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_hold_period = float(np.mean([trade.hold_candles for trade in closed_trades]))
    expectancy = total_pnl / len(closed_trades)
    pnl_color = ui.pnl_color(total_pnl)

    max_drawdown, max_drawdown_pct = compute_drawdown(closed_trades)
    sharpe_ratio  = compute_sharpe(closed_trades, timeframe)
    sortino_ratio = compute_sortino(closed_trades, timeframe)
    max_win_streak, max_loss_streak = max_streaks(closed_trades)
    best_trade  = max(closed_trades, key=lambda trade: trade.pnl_usdt or 0.0)
    worst_trade = min(closed_trades, key=lambda trade: trade.pnl_usdt or 0.0)

    # Wrap stats in a Mission Report Panel
    lines = [
        f"  Trades      : {len(closed_trades)}  ({len(winning_trades)} W / {len(losing_trades)} L)",
        f"  Win Rate    : {win_rate:.1f}%  [{ui.braille_progress_bar(win_rate, width=15)}]",
        f"  Total PnL   : {pnl_color}{total_pnl:+.4f} USDT{Style.RESET_ALL}",
        f"  Expectancy  : {pnl_color}{expectancy:+.4f} USDT/trade{Style.RESET_ALL}",
        f"  Avg Win     : {Fore.GREEN}{avg_win:+.4f}{Style.RESET_ALL}  Avg Loss: {Fore.RED}{avg_loss:+.4f}{Style.RESET_ALL}",
        f"  Prof. Factor: {profit_factor:.2f} | Avg Hold: {avg_hold_period:.1f} candles",
        "",
        ui.hr_dash(width=60),
        "",
        ui.cyber_telemetry("Max DD", max_drawdown, max(1.0, abs(total_pnl)), "$"),
        f"    ({max_drawdown_pct:.1f}% risk exposure)",
        ui.cyber_telemetry("Sharpe", sharpe_ratio if not np.isnan(sharpe_ratio) else 0.0, 3.0, ""),
        ui.cyber_telemetry("Sortino", sortino_ratio if not np.isnan(sortino_ratio) else 0.0, 5.0, ""),
        "",
        f"  Max Streak  : {Fore.GREEN}{max_win_streak}W{Style.RESET_ALL} / {Fore.RED}{max_loss_streak}L{Style.RESET_ALL}",
        f"  Best Trade  : {Fore.GREEN}{best_trade.pnl_usdt:+.4f}{Style.RESET_ALL} ({best_trade.symbol})",
        f"  Worst Trade : {Fore.RED}{worst_trade.pnl_usdt:+.4f}{Style.RESET_ALL} ({worst_trade.symbol})"
    ]

    print("\n" + ui.glow_panel(f"MISSION REPORT: {label or 'BACKTEST RESULTS'}", lines, color_rgb=(255, 128, 0), width=80))


    # Direction breakdown
    for dir_label, group in [("LONG", [t for t in closed_trades if t.direction == "LONG"]),
                              ("SHORT",[t for t in closed_trades if t.direction == "SHORT"])]:
        if not group: continue
        g_wr  = len([t for t in group if t.pnl_usdt > 0]) / len(group) * 100
        g_pnl = sum(t.pnl_usdt for t in group)
        g_exp = g_pnl / len(group)
        dc    = Fore.GREEN if g_pnl >= 0 else Fore.RED
        print(f"  {dir_label:<6}: {len(group):3} trades | WR {g_wr:.0f}% "
              f"| PnL {dc}{g_pnl:+.4f}{Style.RESET_ALL} | exp {dc}{g_exp:+.4f}{Style.RESET_ALL}")

    print()
    # Exit reason breakdown
    for reason in ["trail_stop", "hard_stop", "take_profit", "max_hold", "end_of_data"]:
        group = [t for t in closed_trades if t.exit_reason == reason]
        if not group: continue
        g_wr  = len([t for t in group if t.pnl_usdt > 0]) / len(group) * 100
        g_pnl = sum(t.pnl_usdt for t in group)
        rc    = Fore.GREEN if g_pnl >= 0 else Fore.RED
        print(f"  {reason:<14}: {len(group):3} trades | WR {g_wr:.0f}% | PnL {rc}{g_pnl:+.4f}{Style.RESET_ALL}")

    # Score tier breakdown
    print(Fore.CYAN + f"\n  {'─'*64}")
    print("  SCORE TIER BREAKDOWN:")
    tiers = [(145, 999, "145+"), (120, 144, "120-144"),
             (100, 119, "100-119"), (80, 99, "80-99"), (0, 79, "<80")]
    for lo, hi, tlabel in tiers:
        group = [t for t in closed_trades if lo <= t.score <= hi]
        if len(group) < 2: continue
        g_wr  = len([t for t in group if t.pnl_usdt > 0]) / len(group) * 100
        g_exp = sum(t.pnl_usdt for t in group) / len(group)
        wc    = Fore.GREEN if g_wr >= 50 else Fore.RED
        ec    = Fore.GREEN if g_exp >= 0 else Fore.RED
        print(f"  {tlabel:>8}: [{wc}{draw_bar(g_wr)}{Style.RESET_ALL}] "
              f"{g_wr:4.0f}% WR | {len(group):3} trades | "
              f"exp {ec}{g_exp:+.4f}{Style.RESET_ALL}")

    # Signal type analysis
    print(Fore.CYAN + f"\n  {'─'*64}")
    print("  SIGNAL → OUTCOME ANALYSIS  (n ≥ 3 only):")
    signal_groups = [
        ("RSI oversold",        ["extremely oversold", "RSI.*oversold", "RSI.*recovering"]),
        ("RSI overbought",      ["extremely overbought", "rollover zone"]),
        ("BB lower",            ["BB lower", "below BB lower"]),
        ("BB upper",            ["BB upper", "above BB upper"]),
        ("Bullish Divergence",  ["Bullish Divergence"]),
        ("Bearish Divergence",  ["Bearish Divergence"]),
        ("HTF Alignment",       ["HTF Alignment"]),
        ("Volume spike",        ["Volume spike"]),
        ("Negative Funding",    ["Negative Funding"]),
        ("Positive Funding",    ["Positive Funding", "crowded longs"]),
        ("Low Liquidity",       ["Low Liquidity"]),
        ("EMA stretch below",   ["below EMA21"]),
        ("EMA stretch above",   ["above EMA21"]),
        ("Crash/Dip",           ["crash", "dip \\(oversold"]),
        ("Pump",                ["pump \\(overbought", "rally \\(fade"]),
        ("Near 24h low",        ["Near 24h low"]),
        ("Near 24h high",       ["Near 24h High", "Close to 24h High"]),
    ]
    for slabel, patterns in signal_groups:
        def has_signal(t, pats=patterns):
            return any(
                any(re.search(p, s, re.IGNORECASE) for p in pats)
                for s in t.signals
            )
        group = [t for t in closed_trades if has_signal(t)]
        if len(group) < 3: continue
        g_wr  = len([t for t in group if t.pnl_usdt > 0]) / len(group) * 100
        g_exp = sum(t.pnl_usdt for t in group) / len(group)
        wc = Fore.GREEN if g_wr >= 50 else Fore.RED
        ec = Fore.GREEN if g_exp >= 0 else Fore.RED
        print(f"  {slabel:<22}: {wc}{g_wr:4.0f}% WR{Style.RESET_ALL} "
              f"| {ec}{g_exp:+.5f} exp{Style.RESET_ALL} | n={len(group)}")

    # Low-liq vs normal
    print(Fore.CYAN + f"\n  {'─'*64}")
    for ll_label, ll_val in [("Normal liquidity", False), ("Low liquidity", True)]:
        group = [t for t in closed_trades if t.is_low_liq == ll_val]
        if not group: continue
        g_wr  = len([t for t in group if t.pnl_usdt > 0]) / len(group) * 100
        g_pnl = sum(t.pnl_usdt for t in group)
        lc    = Fore.GREEN if g_pnl >= 0 else Fore.RED
        print(f"  {ll_label:<22}: WR {g_wr:.0f}% | PnL {lc}{g_pnl:+.4f}{Style.RESET_ALL} | n={len(group)}")


def print_per_symbol_stats(trades: List[Trade], top_n: int = 20):
    """Print a per-symbol performance table sorted by total PnL."""
    closed_trades = [trade for trade in trades if trade.pnl_usdt is not None and trade.exit_reason != "open"]
    if not closed_trades:
        return

    from collections import defaultdict
    sym_map: Dict[str, List[Trade]] = defaultdict(list)
    for trade in closed_trades:
        sym_map[trade.symbol].append(trade)

    rows = []
    for sym, symbol_trades in sym_map.items():
        wins_   = [trade for trade in symbol_trades if trade.pnl_usdt > 0]
        pnl     = sum(trade.pnl_usdt for trade in symbol_trades)
        wr      = len(wins_) / len(symbol_trades) * 100
        exp     = pnl / len(symbol_trades)
        rows.append((sym, len(symbol_trades), wr, pnl, exp))

    rows.sort(key=lambda row: row[3], reverse=True)   # sort by total PnL

    print(Fore.CYAN + f"\n{'═'*70}")
    print(Fore.CYAN + Style.BRIGHT + f"  PER-SYMBOL BREAKDOWN  (top/bottom {top_n}, sorted by PnL)")
    print(Fore.CYAN + f"{'═'*70}")
    print(f"  {'Symbol':<16} {'Trades':>7} {'WR%':>6} {'PnL':>11} {'Exp/Trade':>11}")
    print(f"  {'─'*55}")

    display = rows[:top_n]
    if len(rows) > top_n * 2:
        display += [None]   # separator
        display += rows[-top_n:]

    for row in display:
        if row is None:
            print(f"  {'  ···':^55}")
            continue
        sym, n, wr, pnl, exp = row
        pc  = Fore.GREEN if pnl >= 0 else Fore.RED
        wrc = Fore.GREEN if wr >= 50 else Fore.RED
        print(f"  {sym:<16} {n:>7} "
              f"{wrc}{wr:>5.1f}%{Style.RESET_ALL} "
              f"{pc}{pnl:>+10.4f}{Style.RESET_ALL} "
              f"{pc}{exp:>+10.4f}{Style.RESET_ALL}")


def print_sweep_results(results: List[SweepResult], top_n: int = 15):
    print(Fore.CYAN + f"\n{'═'*100}")
    print(Fore.CYAN + Style.BRIGHT + f"  PARAMETER SWEEP — TOP {top_n} BY EXPECTANCY")
    print(Fore.CYAN + f"{'═'*100}")
    print(f"  {'Trail%':>6} {'SL%':>6} {'TP%':>6} {'MinScore':>9} {'Lev':>4} {'Trades':>7} "
          f"{'WR%':>6} {'PnL':>10} {'PF':>6} {'Exp/Trade':>10} {'MaxDD':>9}")
    print(f"  {'─'*96}")
    for r in results[:top_n]:
        pc   = Fore.GREEN if r.total_pnl >= 0 else Fore.RED
        pf_c = Fore.GREEN if r.profit_factor >= 1.5 else (Fore.YELLOW if r.profit_factor >= 1.0 else Fore.RED)
        wr_c = Fore.GREEN if r.win_rate >= 50 else Fore.RED
        dd_c = Fore.RED if r.max_drawdown > abs(r.total_pnl) * 0.5 else Fore.YELLOW
        pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 99 else "∞"
        print(
            f"  {r.trail_pct*100:>5.1f}% "
            f"{r.stop_loss_pct*100:>5.1f}% "
            f"{r.take_profit_pct*100:>5.1f}% "
            f"{r.min_score:>9} "
            f"{r.leverage:>4}x "
            f"{r.total_trades:>7} "
            f"{wr_c}{r.win_rate:>5.1f}%{Style.RESET_ALL} "
            f"{pc}{r.total_pnl:>+9.2f}{Style.RESET_ALL} "
            f"{pf_c}{pf_str:>6}{Style.RESET_ALL} "
            f"{pc}{r.expectancy:>+9.4f}{Style.RESET_ALL} "
            f"{dd_c}{r.max_drawdown:>8.2f}{Style.RESET_ALL} "
        )


def save_trades_csv(trades: List[Trade], path: str):
    """Export closed trades to CSV."""
    closed_trades = [trade for trade in trades if trade.pnl_usdt is not None and trade.exit_reason != "open"]
    if not closed_trades:
        return
    fields = ["symbol", "direction", "score", "entry_price", "exit_price",
              "pnl_usdt", "pnl_pct", "hold_candles", "exit_reason",
              "slippage_pct", "leverage", "margin", "trail_pct", "is_low_liq"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for trade in closed_trades:
            w.writerow({k: getattr(trade, k) for k in fields})
    print(Fore.GREEN + f"  CSV saved → {path}")

# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="FancyFangBot Backtester v2.0 — walk-forward signal replay on real OHLCV",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # ── Core settings ─────────────────────────────────────────────────
    parser.add_argument("--symbols",    nargs="+", default=[],
                        help="Specific symbols to test")
    parser.add_argument("--timeframe",  default="4H")
    parser.add_argument("--candles",    type=int,   default=500,
                        help="Historical candles per symbol")
    parser.add_argument("--min-score",  type=float, default=25.0)
    parser.add_argument("--min-signals", type=int, default=3, help="Minimum number of signals required to consider a candidate (default: 3)")
    parser.add_argument("--trail-pct",  type=float, default=0.02)
    parser.add_argument("--leverage",   type=int,   default=30)
    parser.add_argument("--margin",     type=float, default=5.0,
                        help="USDT margin per trade")
    parser.add_argument("--max-margin", type=float, default=150.0,
                        help="Max total USDT margin for scaling in.")
    parser.add_argument("--max-hold",   type=int,   default=96,
                        help="Max candles to hold a trade")
    parser.add_argument("--min-vol",    type=float,   default=5_000_000.0)
    parser.add_argument("--workers",    type=int,   default=30,
                        help="Parallel fetch workers")

    # ── NEW: Risk / filter options ────────────────────────────────────
    parser.add_argument("--stop-loss-pct", type=float, default=0.0,
                        help="Hard stop loss %% from entry (0 = disabled, e.g. 0.03 = 3%%)")
    parser.add_argument("--take-profit-pct", type=float, default=0.0,
                        help="Take profit %% from entry (0 = disabled, e.g. 0.05 = 5%%)")
    parser.add_argument("--cooldown",   type=int,   default=0,
                        help="Min candles between trades on same symbol (re-entry guard)")
    parser.add_argument("--direction",  default="BOTH",
                        choices=["LONG", "SHORT", "BOTH"],
                        help="Only take LONG or SHORT trades, or BOTH")
    parser.add_argument("--min-score-gap", type=float, default=0.0,
                        help="Min score gap between LONG and SHORT to avoid ambiguous entries")

    # ── Sweep ─────────────────────────────────────────────────────────
    parser.add_argument("--sweep",      action="store_true",
                        help="Run parameter grid sweep")
    parser.add_argument("--sweep-n",    type=int,   default=25,
                        help="Symbol count for sweep")

    # ── Output ────────────────────────────────────────────────────────
    parser.add_argument("--output",     default="backtest_results.json")
    parser.add_argument("--csv",        action="store_true",
                        help="Also save trade log as CSV alongside JSON output")
    parser.add_argument("--no-htf",     action="store_true",
                        help="Skip 1H RSI fetch (faster)")
    parser.add_argument("--window",     type=int,   default=100,
                        help="Window size for signal scanning (default: 100)")
    args = parser.parse_args()

    print(Fore.CYAN + BANNER)

    # Print active settings summary
    flags = []
    if args.stop_loss_pct > 0:
        flags.append(f"hard-stop {args.stop_loss_pct*100:.1f}%")
    if args.cooldown > 0:
        flags.append(f"cooldown {args.cooldown}c")
    if args.direction != "BOTH":
        flags.append(f"direction={args.direction}")
    if args.min_score_gap > 0:
        flags.append(f"score-gap≥{args.min_score_gap}")
    if args.window != 100:
        flags.append(f"window={args.window}")
    if flags:
        print(Fore.YELLOW + f"  Active options: {' | '.join(flags)}\n")

    # ── Symbol universe ───────────────────────────────────────────────
    if args.symbols:
        symbols = args.symbols
    else:
        print(Fore.WHITE + "  Fetching ticker universe...", end="", flush=True)
        tickers = get_tickers(min_vol=args.min_vol)
        tickers.sort(key=lambda t: float(t.get("turnoverRv") or 0), reverse=True)
        n = args.sweep_n # Always use sweep_n to limit symbols
        symbols = [t["symbol"] for t in tickers[:n]]
        print(f" {len(symbols)} symbols (vol ≥ ${args.min_vol:,.0f})")

    print(Fore.WHITE + f"  Fetching {args.candles}x {args.timeframe} candles"
          f"{' + 1H RSI' if not args.no_htf else ''}...")
    print(Fore.WHITE + f"  (This takes ~{max(5, len(symbols)//3)}s with {args.workers} workers)\n")

    # ── Parallel data fetch ───────────────────────────────────────────
    sym_data = []
    lock = threading.Lock()
    done_count = [0]

    def fetch(sym):
        candles = get_candles(sym, timeframe=args.timeframe, limit=args.candles)
        spread  = get_spread_pct(sym)
        funding = get_funding(sym)
        rsi_1h  = None if args.no_htf else get_htf_rsi(sym)
        with lock:
            sym_data.append((sym, candles, spread, funding, rsi_1h))
            done_count[0] += 1
            print(f"\r  Fetching data: {done_count[0]}/{len(symbols)} symbols", end="", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        ex.map(fetch, symbols)
    print()

    valid = [(s, c, sp, f, r) for s, c, sp, f, r in sym_data if len(c) >= 110]
    print(Fore.WHITE + f"  {len(valid)}/{len(symbols)} symbols with sufficient data\n")
    if not valid:
        print(Fore.RED + "  No valid data — check your BASE_URL and network.")
        print(Fore.YELLOW + f"  BASE_URL being used: {BASE_URL}")
        print(Fore.YELLOW + f"  Symbols attempted: {symbols[:5]}")
        print(Fore.YELLOW + f"  sym_data entries: {len(sym_data)} | candle counts: {[len(c) for _,c,*_ in sym_data[:5]]}")
        return

    # Shared kwargs for backtest_symbol
    bt_kwargs = dict(
        margin=args.margin, max_margin=args.max_margin, max_hold=args.max_hold,
        hard_stop_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        cooldown=args.cooldown,
        direction=args.direction, min_score_gap=args.min_score_gap,
        min_signals=args.min_signals,
        window=args.window,
    )

    # Warning about overridden leverage
    if args.leverage != 30: # 30 is the default
        print(Fore.YELLOW + f"  [NOTE] --leverage {args.leverage}x specified, but it will be overridden by")
        print(Fore.YELLOW + "         dynamic ATR-based sizing in pick_sim_leverage().")

    # ── Sweep or single run ───────────────────────────────────────────
    if args.sweep:
        print(Fore.CYAN + Style.BRIGHT + f"  🔍 PARAMETER SWEEP ({args.direction})\n")
        trail_pcts  = [0.005, 0.01, 0.015, 0.02]
        sl_pcts     = [0.0, 0.03, 0.05]
        tp_pcts     = [0.0]
        min_scores  = [40, 60, 80, 100, 120]
        leverages   = [30]

        # Remove parameters that are part of the sweep grid to avoid multiple values
        sweep_kwargs = bt_kwargs.copy()
        for p in ["min_score", "trail_pct", "leverage", "hard_stop_pct", "take_profit_pct"]:
            sweep_kwargs.pop(p, None)

        sweep_res = sweep(valid, trail_pcts, sl_pcts, tp_pcts, min_scores, leverages, **sweep_kwargs)
        print_sweep_results(sweep_res, top_n=15)

        # Detailed stats for the top config
        if sweep_res:
            best = sweep_res[0]
            print(Fore.CYAN + Style.BRIGHT + "\n  Running detailed analysis on best config...")
            best_trades = []

            # Use separate kwargs to avoid duplicates
            analysis_kwargs = bt_kwargs.copy()
            for p in ["min_score", "trail_pct", "leverage", "hard_stop_pct", "take_profit_pct"]:
                analysis_kwargs.pop(p, None)

            for sym, candles, spread, funding, rsi_1h in valid:
                best_trades.extend(backtest_symbol(
                    sym, candles, spread, funding, rsi_1h,
                    min_score=best.min_score, trail_pct=best.trail_pct,
                    leverage=best.leverage,
                    hard_stop_pct=best.stop_loss_pct,
                    take_profit_pct=best.take_profit_pct,
                    **analysis_kwargs,
                ))
            print_stats(best_trades,
                label=f"SL {best.stop_loss_pct*100:.1f}% | TP {best.take_profit_pct*100:.1f}% | Score ≥{best.min_score} | {best.leverage}x lev",
                timeframe=args.timeframe)
            print_per_symbol_stats(best_trades)

        Path(args.output).write_text(json.dumps([
            {"trail_pct": r.trail_pct, "sl_pct": r.stop_loss_pct, "tp_pct": r.take_profit_pct,
             "min_score": r.min_score, "leverage": r.leverage,
             "total_trades": r.total_trades, "win_rate": r.win_rate, "total_pnl": r.total_pnl,
             "profit_factor": r.profit_factor if r.profit_factor < 9999 else 9999,
             "expectancy": r.expectancy, "max_drawdown": r.max_drawdown}
            for r in sweep_res
        ], indent=2))

    else:
        all_trades = []
        for sym, candles, spread, funding, rsi_1h in valid:
            all_trades.extend(backtest_symbol(
                sym, candles, spread, funding, rsi_1h,
                min_score=args.min_score, trail_pct=args.trail_pct,
                leverage=args.leverage, **bt_kwargs,
            ))

        label = (f"Trail {args.trail_pct*100:.1f}% | Score ≥{args.min_score} "
                 f"| {args.leverage}x | {args.timeframe} | {args.candles} candles")
        print_stats(all_trades, label=label, timeframe=args.timeframe)
        print_per_symbol_stats(all_trades)

        # Individual trade log
        closed_trades = [trade for trade in all_trades if trade.pnl_usdt is not None]
        if closed_trades:
            print(Fore.CYAN + "\n  TRADE LOG (worst → best, last 40):")
            print(f"  {'Symbol':<14} {'Dir':>5} {'Score':>6} {'PnL':>9} "
                  f"{'Hold':>6} {'Slip%':>6} {'Exit':<14} {'LowLiq':>6}")
            print(f"  {'─'*76}")
            for trade in sorted(closed_trades, key=lambda x: x.pnl_usdt or 0)[-40:]:
                pc_color = Fore.GREEN if (trade.pnl_usdt or 0) > 0 else Fore.RED
                print(
                    f"  {trade.symbol:<14} {trade.direction:>5} {trade.score:>6} "
                    f"{pc_color}{trade.pnl_usdt:>+8.4f}{Style.RESET_ALL} "
                    f"{trade.hold_candles:>5}c {trade.slippage_pct:>5.3f}% "
                    f"{trade.exit_reason:<14} {'⚠' if trade.is_low_liq else '  '}"
                )

        Path(args.output).write_text(json.dumps([
            {"symbol": trade.symbol, "direction": trade.direction, "score": trade.score,
             "entry": trade.entry_price, "exit": trade.exit_price,
             "pnl_usdt": trade.pnl_usdt, "pnl_pct": trade.pnl_pct,
             "hold_candles": trade.hold_candles, "exit_reason": trade.exit_reason,
             "signals": trade.signals, "slippage_pct": trade.slippage_pct,
             "is_low_liq": trade.is_low_liq}
            for trade in closed_trades
        ], indent=2))

        if args.csv:
            csv_path = str(Path(args.output).with_suffix(".csv"))
            save_trades_csv(all_trades, csv_path)

    print(Fore.GREEN + f"\n  Results saved → {args.output}\n")

if __name__ == "__main__":
    main()