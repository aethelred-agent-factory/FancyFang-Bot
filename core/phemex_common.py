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
Phemex Common Infrastructure
----------------------------
Shared utilities, API wrappers, and indicators for Phemex scanners.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime
import json
import logging
import math
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests
from colorama import Fore, Style
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Exchange Constants ───────────────────────────────────────────────
TAKER_FEE = 0.0006  # 0.06% standard taker fee for Phemex contracts
# Banner is defined in banner.py — single source of truth for the project name graphic.
# See NAME.md for the full name origin story.

# ----------------------------
# EXCEPTIONS
# ----------------------------

class InitializationError(Exception):
    """Raised when a critical dependency or configuration is missing at startup."""
    pass

# ----------------------------
# CONFIG & CONSTANTS
# ----------------------------
BASE_URL = os.getenv("PHEMEX_BASE_URL", "https://api.phemex.com")
FUNDING_FILTER_ENABLED = os.getenv("BOT_FUNDING_FILTER", "true").lower() == "true"
WEEKEND_GUARD_ENABLED = os.getenv("BOT_WEEKEND_GUARD", "true").lower() == "true"

TIMEFRAME_MAP = {
    "1m":  60,    "3m":  180,   "5m":  300,   "15m": 900,
    "30m": 1800,  "1H":  3600,  "2H":  7200,  "4H":  14400,
    "6H":  21600, "12H": 43200, "1D":  86400, "1W":  604800,
}

DEFAULTS = {
    "MIN_VOLUME": int(os.getenv("MIN_VOLUME", 1_000_000)),
    "TIMEFRAME": os.getenv("TIMEFRAME", "15m"),
    "TOP_N": int(os.getenv("TOP_N", 20)),
    "MIN_SCORE": int(os.getenv("MIN_SCORE", 130)),
    "MAX_WORKERS": int(os.getenv("MAX_WORKERS", 100)),
    "RATE_LIMIT_RPS": float(os.getenv("RATE_LIMIT_RPS", 20.0)),
}

# ----------------------------
# System Audit Logger (Async)
# ----------------------------
SYSTEM_AUDIT_LOG = Path(os.path.dirname(os.path.abspath(__file__))).parent / "data" / "logs" / "system_audit.log"

_audit_queue: queue.Queue = queue.Queue()

def _audit_worker():
    """Background worker to process audit log entries."""
    import traceback
    while True:
        try:
            msg = _audit_queue.get()
            if msg is None:  # Sentinel
                break

            # Ensure the directory exists
            try:
                with open(SYSTEM_AUDIT_LOG, "a", encoding="utf-8") as audit_file:
                    audit_file.write(msg + "\n")
            except Exception as error:
                logging.getLogger("phemex_common").error(f"Audit worker failed to write: {error}\n{traceback.format_exc()}")
        except Exception as error:
            logging.getLogger("phemex_common").error(f"Audit worker loop failed: {error}\n{traceback.format_exc()}")
        finally:
            _audit_queue.task_done()

# Start the background worker as a daemon thread
_audit_thread = threading.Thread(target=_audit_worker, daemon=True)
_audit_thread.start()

def log_system_event(event_type: str, message: str, level: int = logging.INFO):
    """
    Logs a high-level system event to the audit log file and the main logger.
    This ensures a permanent record of all significant system actions.
    Uses a background worker to avoid blocking the hot path on disk I/O.
    """
    # REF: [Tier 2] UTC Standardisation
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] [{event_type.upper()}] {message}"

    # 1. Queue the audit file message
    # REF: [Tier 3] Traceability
    _audit_queue.put(formatted_msg)

    # 2. Also log via the standard logging system so it appears in TUI
    audit_logger = logging.getLogger("system_audit")
    if level == logging.ERROR:
        audit_logger.error(message)
    elif level == logging.WARNING:
        audit_logger.warning(message)
    else:
        audit_logger.info(message)

# ── Centralised score thresholds ──────────────────────────────────────────────
# Single source of truth for all score gating across p_bot, sim_bot, backtest,
# and the scanner modules. Override any via @.env / args at call sites.
SCORE_MIN_DEFAULT    = int(os.getenv("MIN_SCORE", 100))      # standard gate
SCORE_MIN_HTF_BYPASS = int(os.getenv("MIN_SCORE_HTF", 100))  # lower bar with HTF alignment
SCORE_MIN_LOW_LIQ    = int(os.getenv("MIN_SCORE_LOW_LIQ", 135)) # higher bar for low-liquidity assets
SCORE_FAST_TRACK     = int(os.getenv("FAST_TRACK_SCORE", 125)) # immediate-entry threshold
SCORE_EXIT_SIGNAL    = int(os.getenv("EXIT_SIGNAL_SCORE", 100)) # opposite-signal exit threshold
SCORE_GRADE_A        = 75  # grade() boundary
SCORE_GRADE_B        = 60  # grade() boundary
SCORE_GRADE_C        = 45  # grade() boundary

# Time-of-day filter (UTC hours to block)
_blocked_hours_str = os.getenv("BLOCKED_HOURS", "")
BLOCKED_HOURS = [int(h.strip()) for h in _blocked_hours_str.split(",") if h.strip().isdigit()]

def is_hour_blocked() -> bool:
    """Returns True if the current UTC hour is in the BLOCKED_HOURS list."""
    if not BLOCKED_HOURS:
        return False
    current_hour = datetime.datetime.now(datetime.timezone.utc).hour
    return current_hour in BLOCKED_HOURS

CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ENTITY_API_KEY = os.getenv("ENTITY_API_KEY")
ENTITY_API_BASE_URL = os.getenv("ENTITY_API_BASE_URL", "https://acoustic-trade-scan-now.base44.app")
ENTITY_APP_ID = os.getenv("ENTITY_APP_ID", "")  # must be set in .env — no hardcoded fallback

logger = logging.getLogger("phemex_common")
logger.addHandler(logging.NullHandler())

if not ENTITY_APP_ID:
    logger.warning("ENTITY_APP_ID not set — entity logging disabled")

# ----------------------------
# Colored Logging
# ----------------------------
class LogBufferHandler(logging.Handler):
    """Custom logging handler that stores the last N formatted logs in a buffer."""
    def __init__(self, buffer: deque):
        super().__init__()
        self.buffer = buffer

    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)

class ColoredFormatter(logging.Formatter):
    """Custom logging formatter that adds color to different log levels."""

    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA + Style.BRIGHT,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, Fore.WHITE)
        record.levelname = f"{color}{record.levelname}{Style.RESET_ALL}"
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return super().format(record)

def setup_colored_logging(logger_name: str, level: int = logging.INFO, log_file: Optional[str] = None, buffer: Optional[deque] = None):
    """Sets up a logger with a colored console handler, optional file handler, and optional buffer handler."""
    # REF: [Tier 3] Descriptive Naming
    new_logger = logging.getLogger(logger_name)
    new_logger.setLevel(level)

    # Avoid duplicate handlers
    if new_logger.hasHandlers():
        new_logger.handlers.clear()

    # Formatter
    formatter = ColoredFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    new_logger.addHandler(console_handler)

    # Buffer Handler (for TUI)
    if buffer is not None:
        buffer_handler = LogBufferHandler(buffer)
        buffer_handler.setFormatter(formatter)
        new_logger.addHandler(buffer_handler)

    # File Handler
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        new_logger.addHandler(file_handler)

    return new_logger

# ----------------------------
# Data classes
# ----------------------------
@dataclass
class TickerData:
    inst_id: str
    price: float
    rsi: Optional[float]
    prev_rsi: Optional[float]
    bb: Optional[Dict[str, float]]
    ema21: Optional[float]
    change_24h: Optional[float]
    funding_rate: Optional[float]
    patterns: List[Tuple[str, int, float]]
    # These fields accommodate both directions
    dist_low_pct: Optional[float] = None
    dist_high_pct: Optional[float] = None
    vol_spike: float = 1.0
    has_div: bool = False
    rsi_1h: Optional[float] = None
    rsi_4h: Optional[float] = None
    fr_change: float = 0.0
    spread: Optional[float] = None
    dist_to_node_below: Optional[float] = None   # Support
    dist_to_node_above: Optional[float] = None   # Resistance
    ema_slope: Optional[float] = None
    slope_change: Optional[float] = None
    news_count: int = 0
    news_titles: List[str] = field(default_factory=list)
    raw_ohlc: List[Tuple[float, float, float, float, float]] = field(default_factory=list)
    vol_24h: float = 0.0
    regime: str = "UNKNOWN"
    entropy: float = 0.0
    kalman_slope: float = 0.0
    ob_imbalance: Optional[float] = None
    ema200: Optional[float] = None

# ----------------------------
# Thread-local session
# ----------------------------
_thread_local = threading.local()

_news_cache: Dict[str, Tuple[int, List[str]]] = {}
_news_cache_lock = threading.Lock()
_news_rate_lock = threading.Lock()
_news_last_request = [0.0]
NEWS_RATE_LIMIT_SECONDS = 1.1

def build_session(timeout: int = 15, max_retries: int = 3) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json",
    })
    retry = Retry(
        total=max_retries,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess

def get_thread_session() -> requests.Session:
    if getattr(_thread_local, "session", None) is None:
        _thread_local.session = build_session()
    return _thread_local.session

# ----------------------------
# Rate limiting
# ----------------------------
_rate_lock = threading.Lock()
_last_request_time_global = 0.0
_global_backoff_until = 0.0  # Timestamp until which all requests are paused

class TokenBucket:
    """Simple thread-safe token bucket implementation."""
    def __init__(self, capacity: float, fill_rate: float):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = threading.Lock()

    def consume(self, weight: float = 1.0) -> float:
        with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.last_update = now

            if self.tokens >= weight:
                self.tokens -= weight
                return 0.0
            else:
                wait_time = (weight - self.tokens) / self.fill_rate
                return wait_time

class WeightedRateLimiter:
    """Manages separate rate limits for different API groups (Contract vs Others)."""
    def __init__(self):
        # Initialise buckets with Phemex-aligned defaults
        self.buckets = {
            "CONTRACT": TokenBucket(capacity=40, fill_rate=20), # Private/Trading (e.g., /g-orders)
            "MARKET":   TokenBucket(capacity=60, fill_rate=30), # Public MD (e.g., /md/v2/orderbook)
            "DEFAULT":  TokenBucket(capacity=20, fill_rate=10),
        }

    def _get_category(self, url: str) -> str:
        u = url.lower()
        if "/g-" in u or "/contract" in u:
            return "CONTRACT"
        if "/md/" in u or "/public/" in u or "ticker" in u or "kline" in u:
            return "MARKET"
        return "DEFAULT"

    def limit(self, url: str, weight: float = 1.0):
        cat = self._get_category(url)
        bucket = self.buckets.get(cat, self.buckets["DEFAULT"])

        while True:
            # Handle global backoff first
            with _rate_lock:
                backoff_until = _global_backoff_until
            now = time.time()
            if now < backoff_until:
                time.sleep(backoff_until - now)
                continue

            wait_time = bucket.consume(weight)
            if wait_time <= 0:
                break
            time.sleep(wait_time)

# Global instance for shared limiting across threads
RATE_LIMITER = WeightedRateLimiter()

def throttle(rps: float) -> None:
    """Sleep as needed to respect the global requests-per-second limit and backoff."""
    if not rps or rps <= 0:
        return
    interval = 1.0 / rps
    global _last_request_time_global, _global_backoff_until

    # 1. Handle global backoff — read atomically under the lock.
    # [T2-03] Loop until no thread has extended the global backoff past our sleep.
    while True:
        with _rate_lock:
            backoff_until = _global_backoff_until
        now = time.time()
        if now >= backoff_until:
            break
        time.sleep(backoff_until - now)
        # Re-check: another thread may have extended _global_backoff_until while we slept.

    # 2. Handle rate limiting
    with _rate_lock:
        now = time.time()
        wait_until = _last_request_time_global + interval
        if now < wait_until:
            sleep_time = wait_until - now
            _last_request_time_global = wait_until
        else:
            sleep_time = 0
            _last_request_time_global = now

    if sleep_time > 0.001:
        time.sleep(sleep_time)

def safe_request(method: str, url: str, params: dict = None, json_data: dict = None,
                 headers: dict = None, rps: float = None, timeout: int = 12,
                 stream: bool = False) -> Optional[requests.Response]:
    global _global_backoff_until
    try:
        # 1. Hierarchical Token-Bucket Limiting (Upgrade)
        RATE_LIMITER.limit(url)

        # 2. Legacy throttle if RPS is explicitly requested
        if rps:
            throttle(rps)

        # Double check backoff outside the lock
        now = time.time()
        if now < _global_backoff_until:
            time.sleep(_global_backoff_until - now)

        sess = get_thread_session()
        resp = sess.request(method, url, params=params, json=json_data,
                            headers=headers, timeout=timeout, stream=stream)

        # REF: Tier 2: Brittle Rate Limit Recovery
        if resp.status_code == 429:
            max_retries = 5
            for attempt in range(max_retries):
                retry_after = resp.headers.get("Retry-After") or resp.headers.get("x-ratelimit-retry-after-Contract")
                wait = float(retry_after) if retry_after else (0.5 * (2 ** attempt))

                # Set global backoff immediately
                with _rate_lock:
                    _global_backoff_until = time.time() + wait

                logger.warning(f"Rate Limit (429) on {url} (attempt {attempt+1}/{max_retries}). Global backoff for {wait}s")

                time.sleep(wait)
                resp = sess.request(method, url, params=params, json=json_data,
                                    headers=headers, timeout=timeout, stream=stream)
                if resp.status_code != 429:
                    break

            if resp.status_code == 429:
                return None

        if resp.status_code >= 400:
            logger.error(f"HTTP {resp.status_code} on {url}: {resp.text[:200]}")
            return None

        resp.raise_for_status()
        return resp
    except Exception as e:
        # Changed to warning so it's visible without debug mode if things are failing
        logger.warning(f"Request failed: {method} {url} -> {e}")
        return None

# ----------------------------
# Simple TTL cache
# ----------------------------
class SimpleCache:
    def __init__(self, ttl: float = 30.0, max_size: int = 1000):
        self._data: Dict[str, Tuple[float, float, Any]] = {}  # (timestamp, ttl, value)
        self._ttl = float(ttl)
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str, ttl_override: float = None):
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            ts, item_ttl, val = entry
            # Use the TTL stored with the item, or override it if requested
            effective_ttl = ttl_override if ttl_override is not None else item_ttl
            if time.time() - ts > effective_ttl:
                del self._data[key]
                return None
            return val

    def set(self, key: str, val: Any, ttl_override: float = None):
        with self._lock:
            # Check for size limit before adding
            if len(self._data) >= self._max_size and key not in self._data:
                # Remove the oldest entry
                oldest_key = min(self._data.keys(), key=lambda k: self._data[k][0])
                del self._data[oldest_key]

            item_ttl = ttl_override if ttl_override is not None else self._ttl
            self._data[key] = (time.time(), item_ttl, val)

CACHE = SimpleCache(ttl=30.0)

# ----------------------------
# Numeric helpers
# ----------------------------
def pct_change(new: float, base: float) -> float:
    """Return percentage change from base to new; returns 0.0 on bad input."""
    try:
        if not base or not math.isfinite(base):
            return 0.0
        return (new - base) / base * 100.0
    except Exception:
        return 0.0

def fmt_vol(volume: float) -> str:
    """Format a volume value into a human-readable K / M / B suffix string."""
    try:
        volume = float(volume)
    except Exception:
        return str(volume)
    if volume >= 1_000_000_000:
        return f"{volume/1_000_000_000:.1f}B"
    if volume >= 1_000_000:
        return f"{volume/1_000_000:.1f}M"
    if volume >= 1_000:
        return f"{volume/1_000:.1f}K"
    return f"{volume:.2f}"

def grade(score: int) -> Tuple[str, str]:
    """Map a raw score to a (letter, colour) tuple."""
    if score >= SCORE_GRADE_A:
        return "A", Fore.GREEN
    if score >= SCORE_GRADE_B:
        return "B", Fore.LIGHTGREEN_EX
    if score >= SCORE_GRADE_C:
        return "C", Fore.YELLOW
    return "D", Fore.RED

def calc_dynamic_threshold(scores: List[int], default_min: int, percentile: int = 90) -> int:
    """
    Calculate a dynamic score threshold based on the distribution of scores.
    Returns the higher of the percentile value or the default minimum.
    """
    if not scores:
        return default_min

    # Use numpy to get the percentile, then floor to int
    dynamic_min = int(np.percentile(scores, percentile))
    final_min = max(dynamic_min, default_min)

    # Weekend Guard: increase threshold by 25% during Saturday/Sunday UTC
    if WEEKEND_GUARD_ENABLED:
        # 5 = Saturday, 6 = Sunday
        if datetime.datetime.now(datetime.timezone.utc).weekday() >= 5:
            final_min = int(final_min * 1.25)
            logger.info("Weekend Guard active: threshold increased to %d", final_min)

    return final_min

# ----------------------------
# Indicator calculations
# ----------------------------
def calc_rsi(closes: List[float], period: int = 14) -> Tuple[Optional[float], Optional[float], List[Optional[float]]]:
    num_closes = len(closes)
    if num_closes <= period:
        return None, None, [None] * num_closes

    price_array = np.asarray(closes, dtype=float)
    price_diffs = np.diff(price_array)
    gains = np.where(price_diffs > 0, price_diffs, 0.0)
    losses = np.where(price_diffs < 0, -price_diffs, 0.0)

    avg_gain = float(gains[:period].sum() / period)
    avg_loss = float(losses[:period].sum() / period)
    rsi_history: List[Optional[float]] = [None] * period

    def rs_to_rsi(gain: float, loss: float) -> float:
        if loss == 0.0:
            return 100.0 if gain > 0 else 50.0
        relative_strength = gain / loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    rsi_history.append(rs_to_rsi(avg_gain, avg_loss))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + float(gains[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses[i])) / period
        rsi_history.append(rs_to_rsi(avg_gain, avg_loss))

    current_rsi = rsi_history[-1]
    previous_rsi = rsi_history[-2] if len(rsi_history) >= 2 else None
    return current_rsi, previous_rsi, rsi_history

def calc_bb(closes: List[float], period: int = 21, mult: float = 2.0) -> Optional[Dict[str, float]]:
    """
    Calculates Bollinger Bands for a given series of close prices.
    REF: [Tier 3] Descriptive Naming
    """
    if len(closes) < period:
        return None
    window_array = np.asarray(closes[-period:], dtype=float)
    middle_band = float(window_array.mean())
    # Use population std (ddof=0) — industry convention for Bollinger Bands
    standard_deviation = float(np.std(window_array, ddof=0))
    upper_band = middle_band + mult * standard_deviation
    lower_band = middle_band - mult * standard_deviation
    width_percentage = (2.0 * mult * standard_deviation / middle_band * 100.0) if middle_band != 0.0 else 0.0
    return {
        "upper": upper_band,
        "mid": middle_band,
        "lower": lower_band,
        "std": standard_deviation,
        "width_pct": width_percentage
    }

def calc_ema_series(closes: List[float], period: int = 21) -> List[float]:
    """
    Calculates an Exponential Moving Average (EMA) series.
    REF: [Tier 3] Descriptive Naming
    """
    num_closes = len(closes)
    if num_closes < period:
        return []
    smoothing_factor = 2.0 / (period + 1.0)
    current_ema = float(sum(closes[:period]) / period)
    ema_series = [current_ema]
    for price in closes[period:]:
        current_ema = (price - current_ema) * smoothing_factor + current_ema
        ema_series.append(current_ema)
    return ema_series

def calc_ema_slope(series: List[float], lookback: int = 3) -> Tuple[Optional[float], Optional[float]]:
    if not series or len(series) <= lookback:
        return None, None
    recent = np.asarray(series[-(lookback + 1):], dtype=float)
    prevs = recent[:-1]
    currs = recent[1:]
    with np.errstate(divide='ignore', invalid='ignore'):
        slopes = np.where(prevs != 0.0, (currs - prevs) / prevs * 100.0, 0.0)
    if slopes.size == 0:
        return None, None
    last_slope = float(slopes[-1])
    delta = float(slopes[-1] - slopes[-2]) if slopes.size > 1 else None
    return last_slope, delta

def calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    num_closes = len(closes)
    if num_closes <= period:
        return None
    highs_array = np.asarray(highs, dtype=float)
    lows_array = np.asarray(lows, dtype=float)
    closes_array = np.asarray(closes, dtype=float)
    true_range_list = []
    for i in range(1, num_closes):
        high_low = highs_array[i] - lows_array[i]
        high_prev_close = abs(highs_array[i] - closes_array[i - 1])
        low_prev_close = abs(lows_array[i] - closes_array[i - 1])
        true_range = float(max(high_low, high_prev_close, low_prev_close))
        true_range_list.append(true_range)
    if len(true_range_list) < period:
        return None
    average_true_range = sum(true_range_list[:period]) / period
    for i in range(period, len(true_range_list)):
        average_true_range = (average_true_range * (period - 1) + true_range_list[i]) / period
    return average_true_range

def calc_market_regime(closes: List[float], period: int = 20) -> Tuple[str, float]:
    """
    Returns (regime, entropy) where regime is 'TRENDING', 'RANGING', or 'VOLATILE'
    entropy is 0.0 (pure trend) to ~3.5+ (pure chaos)
    """
    if len(closes) < period + 1:
        return "UNKNOWN", 0.0

    # [Tier 2] Logic Improvements
    returns = [
        (curr - prev) / prev
        for prev, curr in zip(closes[-period - 1 : -1], closes[-period:])
    ]

    # Bin returns into 8 buckets
    num_bins = 8
    min_return, max_return = min(returns), max(returns)
    return_span = max_return - min_return or 1e-10

    bin_counts = [0] * num_bins
    for ret in returns:
        bin_index = min(num_bins - 1, int((ret - min_return) / return_span * num_bins))
        bin_counts[bin_index] += 1

    # Shannon entropy
    market_entropy = 0.0
    for count in bin_counts:
        if count > 0:
            probability = count / period
            market_entropy -= probability * math.log2(probability)

    max_entropy_value = math.log2(num_bins)  # ~3.0 for 8 bins

    if market_entropy < max_entropy_value * 0.45:
        market_regime = "TRENDING"
    elif market_entropy > max_entropy_value * 0.80:
        market_regime = "VOLATILE"
    else:
        market_regime = "RANGING"

    return market_regime, round(market_entropy, 4)

def calc_kalman_series(
    closes: List[float],
    process_noise: float = 1e-4,
    measurement_noise: float = 1e-2
) -> List[float]:
    """
    Kalman filter price smoother.
    Adaptive: automatically adjusts in volatile conditions.
    REF: [Tier 3] Descriptive Naming
    """
    if not closes:
        return []

    state_estimate = closes[0]
    error_covariance = 1.0
    kalman_series = [state_estimate]

    for observation in closes[1:]:
        # Predict
        error_covariance = error_covariance + process_noise
        # Update
        kalman_gain = error_covariance / (error_covariance + measurement_noise)
        state_estimate = state_estimate + kalman_gain * (observation - state_estimate)
        error_covariance = (1 - kalman_gain) * error_covariance
        kalman_series.append(state_estimate)

    return kalman_series

def calc_kelly_margin(
    bankroll: float,
    win_rate: float,               # e.g. 0.58 for 58%
    average_win: float,            # average winning trade PnL
    average_loss: float,           # average losing trade PnL (positive number)
    kelly_fraction: float = 0.5    # half-Kelly is safer
) -> float:
    """
    Calculates the recommended margin using the Kelly Criterion.
    REF: [Tier 3] Descriptive Naming
    """
    # Not enough history yet or no edge — use flat 2% of bankroll fallback
    if average_loss == 0 or average_win == 0 or win_rate <= 0:
        logging.getLogger("phemex_common").debug("Kelly: Insufficient history, fallback to 2% margin")
        return round(bankroll * 0.02, 2)

    win_loss_ratio = average_win / average_loss
    loss_rate = 1 - win_rate
    kelly_percentage = (win_rate * win_loss_ratio - loss_rate) / win_loss_ratio

    # Kelly went negative — no edge detected yet, use flat fallback
    if kelly_percentage <= 0:
        logging.getLogger("phemex_common").debug(f"Kelly: Negative edge ({kelly_percentage:.4f}), fallback to 2% margin")
        return round(bankroll * 0.02, 2)

    margin = bankroll * kelly_percentage * kelly_fraction
    # Hard cap at 10% bankroll to prevent massive drawdowns from single trade outliers
    return round(min(margin, bankroll * 0.1), 2)

def calc_volume_profile(ohlc: List[Tuple[float, float, float, float, float]], volumes: List[float], bins: int = 20) -> Tuple[Optional[float], List[float]]:
    """
    Calculates the volume profile and identifies Point of Control (POC) and value nodes.
    REF: [Tier 3] Descriptive Naming
    """
    if not ohlc or not volumes or len(ohlc) != len(volumes):
        return None, []
    highs = [candle[1] for candle in ohlc]
    lows = [candle[2] for candle in ohlc]
    min_price = min(lows)
    max_price = max(highs)
    if min_price == max_price:
        return min_price, []
    bin_size = (max_price - min_price) / bins
    profile = [0.0] * bins
    for (_, high, low, _, _), volume in zip(ohlc, volumes):
        low_bin = max(0, int((low - min_price) / bin_size))
        high_bin = min(bins - 1, int((high - min_price) / bin_size))
        bin_span = max(1, high_bin - low_bin + 1)
        for bin_index in range(low_bin, high_bin + 1):
            profile[bin_index] += volume / bin_span
    max_volume = max(profile)
    if max_volume <= 0.0:
        # Fallback to first bin if all volumes are effectively zero
        poc_index = 0
        return min_price + bin_size * (poc_index + 0.5), []
    poc_index = profile.index(max_volume)
    poc_price = min_price + bin_size * (poc_index + 0.5)
    threshold = max_volume * 0.70
    value_nodes = [min_price + bin_size * (i + 0.5) for i, vol in enumerate(profile) if vol >= threshold]
    return poc_price, value_nodes

def calc_volume_spike(volumes: List[float], period: int = 20) -> float:
    """
    Calculates the ratio of the latest volume to the average of the trailing period.
    REF: [Tier 3] Descriptive Naming
    """
    num_volumes = len(volumes)
    if num_volumes <= period:
        return 1.0
    trailing_volumes = np.asarray(volumes[-(period + 1):-1], dtype=float)
    average_volume = float(trailing_volumes.mean()) if trailing_volumes.size > 0 else 0.0
    if average_volume <= 0.0:
        return 1.0
    latest_volume = float(volumes[-1])
    return latest_volume / average_volume

# ══════════════════════════════════════════════════════════════════════════════
# UPGRADE BLOCK — added by the incremental upgrade pass
# ══════════════════════════════════════════════════════════════════════════════

# ── Upgrade #1: Realistic Slippage Simulation ─────────────────────────────────

def calc_slippage(
    price: float,
    direction: str,         # "LONG" or "SHORT"
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    atr: Optional[float] = None,
    slippage_factor: float = 0.5,   # fraction of half-spread or ATR-based estimate
) -> Tuple[float, float]:
    """
    Estimate entry slippage for a market order.

    Priority:
      1. If bid/ask available  → slippage = (ask - bid) / 2 * factor  (cross half-spread)
      2. Fallback              → slippage ≈ ATR * 0.01 (1 % of ATR)

    Args:
        price           : current mid/last price
        direction       : "LONG" (buy at ask) or "SHORT" (sell at bid)
        best_bid        : best bid price from order book
        best_ask        : best ask price from order book
        atr             : ATR(14) value for fallback estimation
        slippage_factor : scaling factor applied to raw half-spread

    Returns:
        (fill_price, slippage_amount)
    """
    slippage_amt = 0.0

    if best_bid is not None and best_ask is not None and best_bid > 0:
        half_spread  = (best_ask - best_bid) / 2.0
        slippage_amt = half_spread * slippage_factor
    elif atr is not None and atr > 0:
        # Rough approximation: 1 % of ATR
        slippage_amt = atr * 0.01
    else:
        # Last resort: 0.02 % of price
        slippage_amt = price * 0.0002

    if direction == "LONG":
        fill_price = price + slippage_amt   # buyer pays more
    else:
        fill_price = price - slippage_amt   # seller receives less

    logger.debug(
        f"slippage: dir={direction} mid={price:.6g} "
        f"slip_amt={slippage_amt:.6g} fill={fill_price:.6g}"
    )
    return fill_price, slippage_amt


# ── Upgrade #2: ATR-Based Stop-Loss and Trailing Stop ────────────────────────

def calc_atr_stops(
    entry_price: float,
    atr: float,
    direction: str,           # "LONG" or "SHORT"
    stop_mult: float = 1.5,
    trail_mult: float = 1.0,
) -> Tuple[float, float]:
    """
    Compute volatility-aware initial stop and trail distance from ATR.

    Returns:
        (stop_price, trail_distance)
    """
    stop_distance  = atr * stop_mult
    trail_distance = atr * trail_mult

    if direction == "LONG":
        stop_price = entry_price - stop_distance
    else:
        stop_price = entry_price + stop_distance

    logger.debug(
        f"atr_stops: entry={entry_price:.6g} atr={atr:.6g} "
        f"stop_mult={stop_mult} trail_mult={trail_mult} "
        f"stop_price={stop_price:.6g} trail_dist={trail_distance:.6g}"
    )
    return stop_price, trail_distance


def update_atr_trail(
    current_price: float,
    stop_price: float,
    high_water: float,
    low_water: float,
    trail_distance: float,
    direction: str,
) -> Tuple[float, float, float]:
    """
    Advance the ATR-based trailing stop based on price movement.

    Args:
        current_price  : latest market price
        stop_price     : current stop-loss level
        high_water     : highest price seen since entry (LONG)
        low_water      : lowest  price seen since entry (SHORT)
        trail_distance : ATR * trail_mult
        direction      : "LONG" or "SHORT"

    Returns:
        (new_stop_price, new_high_water, new_low_water)
    """
    if direction == "LONG":
        if current_price > high_water:
            high_water = current_price
            new_stop   = high_water - trail_distance
            stop_price = max(stop_price, new_stop)  # stops only move up for longs
    else:
        if current_price < low_water:
            low_water = current_price
            new_stop  = low_water + trail_distance
            stop_price = min(stop_price, new_stop)  # stops only move down for shorts

    return stop_price, high_water, low_water


# ── Upgrade #3: Spread Filter ────────────────────────────────────────────────

SPREAD_FILTER_MAX_PCT = float(os.getenv("SPREAD_FILTER_MAX_PCT", "0.20"))  # 0.20 %

def check_spread_filter(
    spread_pct: Optional[float],
    symbol: str = "",
    max_pct: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Returns (pass, reason).
    Pass = True means the spread is acceptable for trading.

    Args:
        spread_pct : bid-ask spread as percentage (e.g. 0.05 for 0.05 %)
        symbol     : instrument name for logging
        max_pct    : optional override for SPREAD_FILTER_MAX_PCT
    """
    if spread_pct is None:
        return True, ""     # no data → allow (don't block on missing data)
    
    limit = max_pct if max_pct is not None else SPREAD_FILTER_MAX_PCT
    
    # Convert from percentage: 0.10 means 0.10 %
    spread_frac = spread_pct / 100.0   # now in decimal (0.001)
    limit_frac  = limit / 100.0
    if spread_frac > limit_frac:
        reason = (
            f"spread {spread_pct:.4f}% > max {limit:.4f}% "
            f"for {symbol}"
        )
        logger.info(f"SPREAD_FILTER: SKIP — {reason}")
        return False, reason
    return True, ""


# ── Upgrade #4: Z-Score Signal Normalisation ─────────────────────────────────

class RollingNormalizer:
    """
    Maintains a rolling window of values and normalises new observations
    using z-score: (x - mean) / std.

    Thread-safe.
    """
    def __init__(self, window: int = 50):
        self._window  = window
        # [T2-06] Use deque(maxlen) for O(1) eviction instead of O(n) list.pop(0)
        self._buf: deque = deque(maxlen=window)
        self._lock = threading.Lock()

    def update_and_score(self, value: float) -> float:
        """
        Append value to the rolling window and return its z-score.
        Returns 0.0 if fewer than 3 samples are available.
        """
        with self._lock:
            self._buf.append(value)   # deque(maxlen) auto-evicts oldest — no manual pop needed
            n = len(self._buf)
            if n < 3:
                return 0.0
            arr  = np.asarray(self._buf, dtype=float)
            mean = float(arr.mean())
            std  = float(arr.std())
            if std < 1e-10:
                return 0.0
            return float((value - mean) / std)

    def reset(self) -> None:
        with self._lock:
            self._buf.clear()


# Shared global normalisers — used by scoring functions
_norm_ema_slope   = RollingNormalizer(window=50)
_norm_volume_spike = RollingNormalizer(window=50)
_norm_rsi_change  = RollingNormalizer(window=50)


def calc_normalised_composite_score(
    ema_slope:    Optional[float],
    vol_spike:    Optional[float],
    rsi_current:  Optional[float],
    rsi_prev:     Optional[float],
    weights: Tuple[float, float, float] = (0.4, 0.3, 0.3),
) -> float:
    """
    Z-score normalised composite signal score.

    Components:
      trend_score    : normalised EMA slope
      volume_score   : normalised volume spike ratio
      momentum_score : normalised RSI change

    Args:
        ema_slope    : EMA slope percentage (from calc_ema_slope)
        vol_spike    : volume spike ratio   (from calc_volume_spike)
        rsi_current  : latest RSI value
        rsi_prev     : previous RSI value
        weights      : (trend_w, volume_w, momentum_w) — must sum to 1.0

    Returns:
        Float composite score (higher = stronger signal in direction)
    """
    rsi_change    = (rsi_current - rsi_prev) if (rsi_current and rsi_prev) else 0.0
    ema_val       = ema_slope   if ema_slope   is not None else 0.0
    vol_val       = vol_spike   if vol_spike   is not None else 1.0

    trend_score    = _norm_ema_slope.update_and_score(ema_val)
    volume_score   = _norm_volume_spike.update_and_score(vol_val)
    momentum_score = _norm_rsi_change.update_and_score(rsi_change)

    w_trend, w_vol, w_mom = weights
    composite = (
        w_trend * trend_score
        + w_vol   * volume_score
        + w_mom   * momentum_score
    )
    return composite


# ── Upgrade #6: Volatility Filter ────────────────────────────────────────────

VOLATILITY_FILTER_MIN = float(os.getenv("VOLATILITY_FILTER_MIN", "0.002"))  # ATR/price

def check_volatility_filter(
    atr: Optional[float],
    price: float,
    symbol: str = "",
    min_ratio: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Returns (pass, reason).
    Pass = True means volatility is high enough to trade.

    Low-volatility conditions often indicate choppy/ranging markets
    where trend signals fire false positives.

    Args:
        atr       : ATR(14) value
        price     : current mid price
        symbol    : instrument name for logging
        min_ratio : optional override for VOLATILITY_FILTER_MIN
    """
    if atr is None or price <= 0:
        return True, ""   # no data → allow
    vol_ratio = atr / price
    
    # Use min_ratio if provided (even if 0.0), otherwise use global constant
    limit = min_ratio if min_ratio is not None else VOLATILITY_FILTER_MIN
    
    if limit > 0 and vol_ratio < limit:
        reason = (
            f"volatility {vol_ratio:.5f} < min {limit:.5f} "
            f"for {symbol}"
        )
        logger.info(f"VOL_FILTER: SKIP — {reason}")
        return False, reason
    return True, ""


# ── Upgrade #10: Order Book Imbalance Signal ──────────────────────────────────

def calc_order_book_imbalance(
    bids: List[List],   # list of [price, qty] from order book
    asks: List[List],   # list of [price, qty] from order book
    depth_levels: int = 5,
) -> Optional[float]:
    """
    Compute order-book imbalance ratio = bid_volume / ask_volume.

    Values > 1.0 → more buy pressure (bullish)
    Values < 1.0 → more sell pressure (bearish)
    Returns None if data unavailable.

    Args:
        bids        : list of [price_str, qty_str] from order book API
        asks        : list of [price_str, qty_str] from order book API
        depth_levels : number of top levels to include
    """
    if not bids or not asks:
        return None
    try:
        bid_vol = sum(float(b[0]) * float(b[1]) for b in bids[:depth_levels])
        ask_vol = sum(float(a[0]) * float(a[1]) for a in asks[:depth_levels])
        if ask_vol <= 0:
            return None
        imbalance = bid_vol / ask_vol
        logger.debug(f"ob_imbalance: bid_vol={bid_vol:.2f} ask_vol={ask_vol:.2f} ratio={imbalance:.4f}")
        return imbalance
    except Exception as e:
        logger.debug(f"calc_order_book_imbalance: error — {e}")
        return None


def get_order_book_with_volumes(symbol: str, rps: float = None) -> Tuple[
    Optional[float], Optional[float], Optional[float], float, Optional[float]
]:
    """
    Extended order book fetch that also returns the imbalance ratio.

    Returns:
        (best_bid, best_ask, spread_pct, depth, imbalance_ratio)
    """
    url = f"{BASE_URL}/md/v2/orderbook"
    resp = safe_request("GET", url, params={"symbol": symbol}, rps=rps)
    if not resp:
        return None, None, None, 0.0, None
    try:
        data = resp.json()
    except Exception:
        return None, None, None, 0.0, None
    if data.get("error") is not None:
        return None, None, None, 0.0, None

    result = data.get("result", {}) or {}
    book   = result.get("orderbook_p", {}) or {}
    bids   = book.get("bids", [])
    asks   = book.get("asks", [])
    if not bids or not asks:
        return None, None, None, 0.0, None

    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except Exception:
        return None, None, None, 0.0, None

    spread_pct = ((best_ask - best_bid) / best_bid * 100.0) if best_bid > 0 else None

    def depth_sum(entries):
        total = 0.0
        for row in entries:
            try:
                total += float(row[0]) * float(row[1])
            except Exception:
                continue
        return total

    depth      = depth_sum(bids) + depth_sum(asks)
    imbalance  = calc_order_book_imbalance(bids, asks)

    return best_bid, best_ask, spread_pct, depth, imbalance


# ── Upgrade #9: Dynamic Pair Selection Helpers ────────────────────────────────

def select_top_pairs(
    tickers: List[Dict],
    top_n: int = 20,
    min_volume: float = 1_000_000,
    min_volatility_pct: float = 0.0,
    atr_cache: Optional[Dict[str, float]] = None,
) -> List[Dict]:
    """
    Pre-filter and rank tickers by a composite of volume and volatility.

    Args:
        tickers             : raw list from get_tickers()
        top_n               : how many to return
        min_volume          : minimum 24h turnover in USD
        min_volatility_pct  : minimum (high-low)/price % change; 0 = no filter
        atr_cache           : optional dict symbol→ATR for volatility scoring

    Returns:
        Sorted list of up to top_n ticker dicts.
    """
    candidates = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT") or symbol.startswith("s"):
            continue
        vol24 = float(t.get("turnoverRv") or 0.0)
        if vol24 < min_volume:
            continue

        last  = float(t.get("lastRp") or t.get("closeRp") or 0.0)
        high  = float(t.get("highRp") or last)
        low   = float(t.get("lowRp")  or last)
        if last > 0 and min_volatility_pct > 0:
            daily_range_pct = (high - low) / last * 100.0
            if daily_range_pct < min_volatility_pct:
                continue

        # Composite rank: normalise log of volume + ATR-based vol score
        vol_score    = math.log1p(vol24)
        atr          = (atr_cache or {}).get(symbol, 0.0)
        atr_score    = (atr / last * 100.0) if (atr and last > 0) else 0.0
        composite    = vol_score * 0.6 + atr_score * 0.4
        candidates.append((composite, t))

    candidates.sort(key=lambda x: x[0], reverse=True)
    selected = [t for _, t in candidates[:top_n]]
    logger.info(
        f"select_top_pairs: {len(tickers)} → {len(selected)} "
        f"(top_n={top_n}, min_vol={min_volume:.0f})"
    )
    return selected


# ══════════════════════════════════════════════════════════════════════════════
# END OF UPGRADE BLOCK
# ══════════════════════════════════════════════════════════════════════════════

# ── New Mathematical Utilities for Adaptive Filtering ────────────────────────

def calc_shannon_entropy_signals(long_count: int, short_count: int, total_scanned: int) -> float:
    """
    Computes Shannon entropy of the signal direction distribution.
    Includes 'NONE' as a category representing no signal.
    H = - sum(p_i * log2(p_i))
    """
    if total_scanned <= 0:
        return 0.0

    n_none = max(0, total_scanned - long_count - short_count)
    counts = [long_count, short_count, n_none]

    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total_scanned
            entropy -= p * math.log2(p)

    return round(entropy, 4)

def calc_hurst_exponent(series: List[float], max_window: int = 50) -> float:
    """
    Estimates the Hurst exponent using a simplified R/S analysis.
    H > 0.5: Persistent (Trending)
    H < 0.5: Anti-persistent (Mean-reverting)
    H = 0.5: Random Walk (Noise)
    """
    if len(series) < 20:
        return 0.5

    # Calculate log returns
    arr = np.array(series)
    returns = np.diff(np.log(arr))

    def rs_analysis(data):
        if len(data) < 4:
            return 0.0
        mean_val = np.mean(data)
        cumulative_sum = np.cumsum(data - mean_val)
        range_val = np.max(cumulative_sum) - np.min(cumulative_sum)
        std_val = np.std(data)
        return range_val / std_val if std_val > 0 else 0.0

    # We use a few window sizes to estimate the slope of log(R/S) vs log(size)
    # For a quick estimation on ~100 candles, we can just use the full range vs half range
    sizes = [len(returns) // 4, len(returns) // 2, len(returns)]
    rs_vals = []
    for s in sizes:
        # Average R/S across non-overlapping windows
        windows = [returns[i:i+s] for i in range(0, len(returns), s) if len(returns[i:i+s]) == s]
        if windows:
            rs_vals.append(np.mean([rs_analysis(w) for w in windows]))
        else:
            rs_vals.append(0.0)

    # Filter out zero R/S values
    valid_idx = [i for i, val in enumerate(rs_vals) if val > 0]
    if len(valid_idx) < 2:
        return 0.5

    log_sizes = np.log([sizes[idx] for idx in valid_idx])
    log_rs_vals = np.log([rs_vals[idx] for idx in valid_idx])

    # Linear regression slope is H
    slope, _ = np.polyfit(log_sizes, log_rs_vals, 1)
    return round(float(np.clip(slope, 0.0, 1.0)), 3)

class HawkesTracker:
    """
    Tracks self-exciting process intensity (Hawkes) for signal directions.
    λ(t) = μ + Σ α * exp(-β * (t - t_i))
    """
    def __init__(self, mu: float = 0.1, alpha: float = 0.8, beta: float = 0.1):
        self.mu = mu
        self.alpha = alpha
        self.beta = beta
        self.last_time = time.time()
        self.intensity = mu
        self._lock = threading.Lock()

    def update(self, event_occurred: bool = True) -> float:
        """Decays intensity and adds pulse if event occurred."""
        with self._lock:
            now = time.time()
            dt = now - self.last_time
            # Decay intensity towards baseline mu
            self.intensity = self.mu + (self.intensity - self.mu) * math.exp(-self.beta * dt)

            if event_occurred:
                self.intensity += self.alpha

            self.last_time = now
            return self.intensity

    def get_intensity(self) -> float:
        return self.update(event_occurred=False)

# ----------------------------
# Phemex API helpers
# ----------------------------
def _resolve_resolution(timeframe: str) -> int:
    """Resolve a timeframe string (e.g. '15m') to its API resolution integer."""
    return TIMEFRAME_MAP.get(timeframe, 900)

def get_tickers(rps: float = None, direction_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch all USDT-M perpetual 24hr tickers.
    Endpoint: GET /md/v3/ticker/24hr/all
    """
    url = f"{BASE_URL}/md/v3/ticker/24hr/all"
    resp = safe_request("GET", url, rps=rps)
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    if data.get("error") is not None:
        logger.debug("Tickers API error: %s", data.get("error"))
        return []
    result = data.get("result", []) or []

    filtered = []
    for ticker in result:
        if not isinstance(ticker, dict):
            continue
        symbol = ticker.get("symbol", "")
        if not symbol.endswith("USDT") or symbol.startswith("s"):
            continue

        # [T1-02] High-speed funding gate (if filter is enabled)
        if direction_filter and FUNDING_FILTER_ENABLED:
            fr_raw = ticker.get("fundingRateRr", "0")
            try:
                fr = float(fr_raw)
                if direction_filter.upper() == "SHORT" and fr < 0:
                    continue # Skip negative funding for shorts
                if direction_filter.upper() == "LONG" and fr > 0:
                    continue # Skip positive funding for longs (optional, but consistent)
            except Exception:
                pass

        filtered.append(ticker)

    filtered.sort(key=lambda x: float(x.get("turnoverRv") or 0.0), reverse=True)
    return filtered

def get_candles(symbol: str, timeframe: str = "15m", limit: int = 100, rps: float = None) -> List[List[Any]]:
    """
    Fetch klines from Phemex public market data endpoint.
    Endpoint: GET /exchange/public/md/v2/kline/last
    """
    resolution = _resolve_resolution(timeframe)
    cache_key = f"candles:{symbol}:{resolution}:{limit}"

    # Adaptive TTL: 300s (5min) for HTF (>= 1H), 30s for LTF
    effective_ttl = 300.0 if resolution >= 3600 else 30.0
    cached = CACHE.get(cache_key, ttl_override=effective_ttl)
    if cached is not None:
        return cached

    # Standard tradable symbols do NOT use a dot prefix for this endpoint
    api_symbol = symbol.replace(".", "")

    # Map custom limits to allowed Phemex limits: [5, 10, 50, 100, 500, 1000]
    allowed_limits = [5, 10, 50, 100, 500, 1000]
    api_limit = next((limit_val for limit_val in allowed_limits if limit_val >= limit), 1000)

    url = f"{BASE_URL}/exchange/public/md/v2/kline/last"
    params = {"symbol": api_symbol, "resolution": resolution, "limit": api_limit}
    
    logger.debug("get_candles: requesting %s with params %s", url, params)
    
    try:
        resp = safe_request("GET", url, params=params, rps=rps)
        if not resp:
            logger.error(f"get_candles: No response for {symbol} {timeframe}")
            return []
        data = resp.json()
        
        if data.get("code") == 0:
            rows = data.get("data", {}).get("rows", [])
            if not rows:
                # ── Recursive Retry Logic ──
                # If we asked for many and got 0, try the next smallest allowed limit
                current_idx = allowed_limits.index(api_limit)
                if current_idx > 0:
                    smaller_limit = allowed_limits[current_idx - 1]
                    logger.debug("get_candles: %s %s returned 0 rows for limit %d. Retrying with limit %d...", 
                                 symbol, timeframe, api_limit, smaller_limit)
                    time.sleep(0.05) # small backoff
                    return get_candles(symbol, timeframe, smaller_limit, rps)
                
                logger.warning(f"get_candles: No rows in data for {symbol} {timeframe} even at minimum limit. Full response: {data}")
                return []

            rows_sorted = sorted(rows, key=lambda r: r[0])
            # Slice to the exact requested amount
            final_rows = rows_sorted[-limit:]
            CACHE.set(cache_key, final_rows, ttl_override=effective_ttl)
            return final_rows
        else:
            logger.error(f"get_candles: API error {data.get('code')} for {symbol} {timeframe}. Full response: {data}")
            return []

    except json.JSONDecodeError as e:

        logger.error(f"get_candles: JSON decode error for {symbol} {timeframe}: {e}. Response text: {resp.text if resp else 'N/A'}")

        return []

    except Exception as e:

        logger.error(f"get_candles: Unexpected error for {symbol} {timeframe}: {e}")

        return []

def get_funding_rate_info(symbol: str, rps: float = None) -> Tuple[Optional[float], Optional[float], float]:
    """
    Fetch current funding rate.
    Endpoint: GET /contract-biz/public/real-funding-rates?symbol=
    Returns (current_fr, prev_fr, delta).
    """
    cache_key = f"funding:{symbol}"
    cached = CACHE.get(cache_key)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/contract-biz/public/real-funding-rates"
    resp = safe_request("GET", url, params={"symbol": symbol}, rps=rps)
    if not resp:
        return None, None, 0.0
    try:
        data = resp.json()
    except Exception:
        return None, None, 0.0

    items = data if isinstance(data, list) else data.get("data", [])
    if not items:
        return _get_funding_rate_history(symbol, rps)

    try:
        entry = None
        if isinstance(items, list):
            for it in items:
                if it.get("symbol") == symbol:
                    entry = it
                    break
            if entry is None and items:
                entry = items[0]
        else:
            entry = items

        current_fr = float(entry.get("fundingRate", 0.0))
        out = (current_fr, current_fr, 0.0)
        CACHE.set(cache_key, out)
        return out
    except Exception:
        return None, None, 0.0

def prefetch_all_funding_rates(rps: float = None):
    """
    Fetch all funding rates in one call and populate CACHE.
    """
    url = f"{BASE_URL}/contract-biz/public/real-funding-rates"
    resp = safe_request("GET", url, rps=rps)
    if not resp:
        return
    try:
        data = resp.json()
        res_data = data.get("data", {})
        if isinstance(res_data, list):
            items = res_data
        else:
            items = res_data.get("rows", [])

        if not items:
            logger.debug("Funding prefetch returned empty: %s", data)
            return

        populated = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol")
            if not sym:
                continue
            fr_raw = item.get("fundingRate") or item.get("fundingRateRr")
            if fr_raw is not None:
                # REF: Tier 3: Non-Descriptive Variable Naming (fr -> funding_rate)
                funding_rate = float(fr_raw)
                CACHE.set(f"funding:{sym}", (funding_rate, funding_rate, 0.0))
                populated += 1

        logger.info("Funding prefetch: %d symbols cached", populated)
    except Exception as e:
        logger.error("Funding prefetch error: %s", e)

def _get_funding_rate_history(symbol: str, rps: float = None) -> Tuple[Optional[float], Optional[float], float]:
    base = symbol.replace("USDT", "")
    fr_symbol = f".{base}USDTFR8H"
    url = f"{BASE_URL}/api-data/public/data/funding-rate-history"
    resp = safe_request("GET", url, params={"symbol": fr_symbol, "limit": 2, "latestOnly": False}, rps=rps)
    if not resp:
        return None, None, 0.0
    try:
        data = resp.json()
        if data.get("code") != 0:
            return None, None, 0.0
        rows = data.get("data", {}).get("rows", [])
        if not rows:
            return None, None, 0.0
        current_fr = float(rows[-1].get("fundingRate", 0.0))
        prev_fr = float(rows[-2].get("fundingRate", current_fr)) if len(rows) > 1 else current_fr
        return current_fr, prev_fr, current_fr - prev_fr
    except Exception:
        return None, None, 0.0

def get_order_book(symbol: str, rps: float = None):
    """
    Endpoint: GET /md/v2/orderbook?symbol=
    Returns (best_bid, best_ask, spread_pct, depth).
    """
    url = f"{BASE_URL}/md/v2/orderbook"
    resp = safe_request("GET", url, params={"symbol": symbol}, rps=rps)
    if not resp:
        return None, None, None, 0.0
    try:
        data = resp.json()
    except Exception:
        return None, None, None, 0.0
    if data.get("error") is not None:
        return None, None, None, 0.0

    result = data.get("result", {}) or {}
    book = result.get("orderbook_p", {}) or {}
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if not bids or not asks:
        return None, None, None, 0.0
    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except Exception:
        return None, None, None, 0.0
    if best_bid == 0.0:
        spread_pct = None
    else:
        spread_pct = (best_ask - best_bid) / best_bid * 100.0

    def depth_sum(entries):
        total = 0.0
        for row in entries:
            try:
                price = float(row[0])
                qty = float(row[1])
                total += price * qty
            except Exception:
                continue
        return total

    return best_bid, best_ask, spread_pct, (depth_sum(bids) + depth_sum(asks))

def get_cryptopanic_news(coin_symbol: str) -> Tuple[int, List[str]]:
    if not CRYPTOPANIC_API_KEY:
        return 0, []

    with _news_cache_lock:
        if coin_symbol in _news_cache:
            return _news_cache[coin_symbol]

    # Use a separate lock for rate limiting to avoid blocking the cache lock
    with _news_rate_lock:
        elapsed = time.time() - _news_last_request[0]
        if elapsed < NEWS_RATE_LIMIT_SECONDS:
            time.sleep(NEWS_RATE_LIMIT_SECONDS - elapsed)
        _news_last_request[0] = time.time()

    try:
        url = "https://cryptopanic.com/api/developer/v2/posts/"
        params = {"auth_token": CRYPTOPANIC_API_KEY, "currencies": coin_symbol, "filter": "news"}
        resp = safe_request("GET", url, params=params)
        if not resp:
            return 0, []
        data = resp.json()
        results = data.get("results", []) or []
        count = data.get("count", len(results))
        titles = [res.get("title", "") for res in results[:5]]
        result = (min(count, 99), titles)
    except Exception as e:
        logger.debug("cryptopanic error: %s", e)
        result = (0, [])

    with _news_cache_lock:
        _news_cache[coin_symbol] = result
    return result

# ----------------------------
# AI & Entity integration
# ----------------------------
# Pre-score gate threshold used inside unified_analyse
# [FIX] Raised from 60 to 80 to tighten initial signal filter and reduce entropy-deflator noise
_PRE_SCORE_GATE_DEFAULT = 40


def unified_analyse(
    ticker: dict,
    cfg: dict,
    direction: str,
    score_func,
    detect_patterns_func,
    detect_div_func,
    calc_confidence_func,
    enable_ai: bool = True,
    enable_entity: bool = True,
    scan_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Unified analysis engine for both LONG and SHORT scanners.

    [T1-02] This is the single source of truth for signal generation.  Both
    phemex_short.analyse() and phemex_long.analyse() delegate here, passing
    direction-specific callbacks.  All upgrade logic (slippage model, volatility
    filter, order book imbalance) is therefore active on every live scan.

    Args:
        ticker               : raw ticker dict from get_tickers()
        cfg                  : config dict (TIMEFRAME, CANDLES, MIN_VOLUME, ...)
        direction            : "LONG" or "SHORT" (passed through to entity hook)
        score_func           : callable(TickerData) -> (int, List[str])
        detect_patterns_func : callable(ohlc) -> List[Tuple[str, int, float]]
        detect_div_func      : callable(closes, rsi_hist) -> bool
        calc_confidence_func : callable(TickerData, score, bb_pct) -> (str, color, notes)
        enable_ai            : gate for DeepSeek thesis generation
        enable_entity        : gate for Entity API persistence
        scan_id              : optional external scan correlation ID
    """
    symbol = ticker.get("symbol")
    if not symbol:
        return None

    try:
        last   = float(ticker.get("lastRp") or ticker.get("closeRp") or 0.0)
        open24 = float(ticker.get("openRp") or last)
        low24  = float(ticker.get("lowRp") or last)
        high24 = float(ticker.get("highRp") or last)
        vol24  = float(ticker.get("turnoverRv") or 0.0)

        min_vol = cfg.get("MIN_VOLUME", 1_000_000)
        if vol24 < min_vol:
            logger.debug("  %s: vol24 %.0f < min_vol %.0f, skipping.", symbol, vol24, min_vol)
            return None
        if last == 0.0:
            logger.debug("  %s: last price is 0.0, skipping.", symbol)
            return None

        # 1. Funding check (direction-specific thresholds handled in score_func or caller)
        # REF: Tier 3: Non-Descriptive Variable Naming (fr -> funding_rate)
        funding_rate, prev_funding_rate, funding_rate_change = get_funding_rate_info(symbol, rps=cfg.get("RATE_LIMIT_RPS"))
        if funding_rate is None:
            fr_raw = ticker.get("fundingRateRr")
            funding_rate = float(fr_raw) if fr_raw is not None else 0.0
            funding_rate_change = 0.0

        # Hard Funding Filter: only allow SHORT if funding is positive (Longs pay Shorts)
        if FUNDING_FILTER_ENABLED and direction == "SHORT" and funding_rate < 0.0:
            logger.debug("  %s: funding is negative (%.4f%%), skipping SHORT entry.", symbol, funding_rate * 100)
            return None

        # 2. Fetch klines
        candles = get_candles(symbol, timeframe=cfg["TIMEFRAME"], limit=cfg.get("CANDLES", 50), rps=cfg.get("RATE_LIMIT_RPS"))
        if not candles:
            logger.debug("  %s: no candles returned for timeframe %s, skipping.", symbol, cfg["TIMEFRAME"])
            return None
        
        logger.debug("  %s: fetched %d candles for timeframe %s", symbol, len(candles), cfg["TIMEFRAME"])

        ohlc, highs, lows, closes, vols = [], [], [], [], []
        for candle in candles:
            try:
                # API mapping: [timestamp, interval, last, open, high, low, close, volume, ...]
                # REF: [Tier 3] Descriptive Naming
                open_px, high_px, low_px, close_px = float(candle[3]), float(candle[4]), float(candle[5]), float(candle[6])
                volume = float(candle[7]) if len(candle) > 7 else 0.0
                ohlc.append((open_px, high_px, low_px, close_px, volume))
                highs.append(high_px)
                lows.append(low_px)
                closes.append(close_px)
                vols.append(volume)
            except Exception:
                continue

        if not closes:
            logger.debug("  %s: no valid close prices parsed from candles, skipping.", symbol)
            return None

        # 3. Indicator calculation (cheap — always executed)
        try:
            rsi, prev_rsi, rsi_hist = calc_rsi(closes)
            bb = calc_bb(closes)
            ema_series = calc_ema_series(closes, 21)
            ema21 = ema_series[-1] if ema_series else None
            ema200_series = calc_ema_series(closes, 200)
            ema200 = ema200_series[-1] if ema200_series else None
            ema_slope, slope_change = calc_ema_slope(ema_series)
            atr = calc_atr(highs, lows, closes)

            # ── Volatility Filter (Upgrade #6) ──────────────────────────────────
            vol_ok, vol_reason = check_volatility_filter(atr, last, symbol, min_ratio=cfg.get("vol_min"))
            if not vol_ok:
                logger.debug("  %s: volatility filter failed: %s, skipping.", symbol, vol_reason)
                return None

            vol_spike = calc_volume_spike(vols)
            regime, entropy = calc_market_regime(closes)
            kalman_series = calc_kalman_series(closes)
            kalman_price  = kalman_series[-1] if kalman_series else None
            kalman_slope  = kalman_series[-1] - kalman_series[-2] if len(kalman_series) >= 2 else 0.0
        except Exception as e:
            logger.error(f"  {symbol}: Indicator calculation failed: {e}")
            return None

        # 4. Expensive API calls
        # [T1-02] Use get_order_book_with_volumes (Upgrade #10 imbalance) — was get_order_book
        best_bid, best_ask, spread, depth, ob_imbalance = get_order_book_with_volumes(symbol, rps=cfg.get("RATE_LIMIT_RPS"))

        # ── Spread Filter (Upgrade #1) ──────────────────────────────────────
        spread_ok, _ = check_spread_filter(spread, symbol, max_pct=cfg.get("spread_max_pct"))
        if not spread_ok:
            return None

        # 5. Volume Profile
        poc_price, nodes = calc_volume_profile(ohlc, vols, bins=20)
        dist_to_node_below, dist_to_node_above = None, None
        if nodes and last > 0:
            nodes_below = [n for n in nodes if n < last]
            nodes_above = [n for n in nodes if n > last]
            if nodes_below:
                dist_to_node_below = abs(pct_change(last, max(nodes_below)))
            if nodes_above:
                dist_to_node_above = abs(pct_change(last, min(nodes_above)))

        # 6. HTF Context (Upgrade #5 — 1H + 4H RSI alignment)
        rsi_1h, rsi_4h = None, None
        c1h = get_candles(symbol, timeframe="1H", limit=50, rps=cfg.get("RATE_LIMIT_RPS"))
        if c1h:
            cl1h = [float(c[6]) for c in c1h]
            if cl1h:
                rsi_1h, _, _ = calc_rsi(cl1h)

        c4h = get_candles(symbol, timeframe="4H", limit=50, rps=cfg.get("RATE_LIMIT_RPS"))
        if c4h:
            cl4h = [float(c[6]) for c in c4h]
            if cl4h:
                rsi_4h, _, _ = calc_rsi(cl4h)

        # 7. Pattern & Divergence detection
        raw_patterns = detect_patterns_func(ohlc)
        has_div = detect_div_func(closes, rsi_hist)

        # 8. Data Aggregation — full TickerData with all upgrade fields populated
        data = TickerData(
            inst_id=symbol, price=last, rsi=rsi, prev_rsi=prev_rsi, bb=bb, ema21=ema21,
            change_24h=pct_change(last, open24), funding_rate=funding_rate, patterns=raw_patterns,
            dist_low_pct=pct_change(last, low24), dist_high_pct=pct_change(last, high24),
            vol_spike=vol_spike, has_div=has_div, rsi_1h=rsi_1h, rsi_4h=rsi_4h,
            fr_change=funding_rate_change or 0.0, spread=spread,
            dist_to_node_below=dist_to_node_below, dist_to_node_above=dist_to_node_above,
            ema_slope=ema_slope, slope_change=slope_change,
            raw_ohlc=ohlc[-10:], vol_24h=vol24,
            regime=regime, entropy=entropy, kalman_slope=kalman_slope,
            ema200=ema200,
        )

        # 9. Scoring
        try:
            score, signals = score_func(data)
        except Exception as e:
            logger.error("  %s: score_func FAILED: %s", symbol, e)
            return None

        # 10. Result construction
        bb_pct = None
        if bb:
            bb_range = bb["upper"] - bb["lower"]
            if bb_range > 0:
                bb_pct = (last - bb["lower"]) / bb_range * 100.0

        confidence, conf_color, conf_notes = calc_confidence_func(data, score, bb_pct)
        stop_pct = (0.5 * atr / last * 100.0) if (atr and last > 0) else None

        # REF: Tier 3: Temporal Inconsistency
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = {
            "inst_id": symbol, "direction": direction, "price": last, "change_24h": data.change_24h,
            "vol_24h": vol24, "rsi": rsi, "prev_rsi": prev_rsi, "bb_pct": bb_pct,
            "ema21": ema21, "funding_pct": funding_rate * 100.0 if funding_rate is not None else None,
            "score": score, "signals": signals, "patterns": raw_patterns,  # [T1-FIX] was `patterns` (NameError)
            "confidence": confidence, "conf_color": conf_color, "conf_notes": conf_notes,
            "dist_low": data.dist_low_pct, "dist_high": data.dist_high_pct,
            "vol_spike": vol_spike, "bb_width": bb["width_pct"] if bb else 0.0,
            "atr_stop_pct": stop_pct, "raw_ohlc": ohlc[-10:], "spread": spread,
            "dist_to_node_below": dist_to_node_below, "dist_to_node_above": dist_to_node_above,
            "ema_slope": ema_slope, "slope_change": slope_change, "fr_change": funding_rate_change,
            "rsi_1h": rsi_1h, "rsi_4h": rsi_4h, "scan_timestamp": now_utc_iso,
            "regime": regime, "entropy": entropy, "kalman_price": kalman_price, "kalman_slope": kalman_slope,
            "ema200": ema200,
            # ── Upgrade fields ────────────────────────────────────────────────
            "best_bid":    best_bid,       # Upgrade #1 slippage / #10 imbalance
            "best_ask":    best_ask,
            "ob_imbalance": ob_imbalance,  # Upgrade #10: order book imbalance ratio
            # ── Audit fields (Upgrade #16) ───────────────────────────────────
            "raw_signals": {
                "rsi": rsi,
                "bb_width": bb["width_pct"] if bb else None,
                "ema_slope": ema_slope,
                "vol_spike": vol_spike,
                "entropy": entropy,
                "kalman_slope": kalman_slope,
                "ob_imbalance": ob_imbalance,
                "funding_rate": funding_rate,
                "rsi_1h": rsi_1h,
                "rsi_4h": rsi_4h
            }
        }

        # 11. Entity API Hook
        if enable_entity and ENTITY_API_KEY:
            pc_res = make_entity_request("ScanResult", method="POST", data={
                "scan_id": scan_id, "timestamp": now_utc_iso,
                "inst_id": symbol, "price": last, "change_24h": data.change_24h or 0.0,
                "rsi": rsi or 50.0, "funding_rate": round(funding_rate * 100, 8) if funding_rate is not None else 0.0,
                "score": score, "signals": signals, "atr_stop_pct": stop_pct or 0.0,
                "vol_spike": vol_spike or 0.0, "spread": spread or 0.0, "direction": direction.capitalize()
            })
            if pc_res and isinstance(pc_res, dict):
                result["entity_id"] = pc_res.get("id")

        return result

    except Exception as e:
        logger.error(f"Error in unified_analyse for {symbol}: {e}")
        return None

def make_entity_request(entity_name: str, method: str = "POST", data: dict = None, entity_id: str = None):
    if not ENTITY_API_KEY:
        return None
    url = f"{ENTITY_API_BASE_URL}/api/apps/{ENTITY_APP_ID}/entities/{entity_name}"
    if entity_id:
        url = f"{url}/{entity_id}"
    headers = {"api_key": ENTITY_API_KEY, "Content-Type": "application/json"}
    try:
        if method.upper() == "GET":
            response = safe_request("GET", url, params=data, headers=headers)
        elif method.upper() == "PUT":
            response = safe_request("PUT", url, json_data=data, headers=headers)
        else:
            response = safe_request("POST", url, json_data=data, headers=headers)
        if not response:
            return None
        return response.json()
    except Exception:
        return None

def call_deepseek(
    prompt: str,
    system_prompt: str = "You are an expert crypto trader and technical analyst. Use plain text formatting.",
    stream: bool = True,
    output_callback=None,
) -> Optional[str]:
    """
    Call the DeepSeek API.

    [T2-04] Streaming tokens are never written directly to stdout, which would
    corrupt any blessed/curses TUI that is currently running.  Instead:
      - If *output_callback* is provided, each token is passed to it.
      - Otherwise tokens are emitted at DEBUG level via the module logger.
    Callers that want to display a streaming response in the terminal should
    pass their own callback (e.g. a wrapper that appends to a TUI log buffer).

    Args:
        prompt          : user prompt text
        system_prompt   : system role instruction
        stream          : whether to use SSE streaming mode
        output_callback : optional callable(token: str) — receives each streamed token
    """
    if not DEEPSEEK_API_KEY:
        return None
    url = "https://api.deepseek.com/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_API_KEY.strip()}"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        "temperature": 0.7,
        "stream": stream
    }
    try:
        resp = safe_request("POST", url, json_data=payload, headers=headers, stream=stream)
        if not resp:
            return None
        if stream:
            full_text = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    data_raw = line_str[len("data: "):]
                    if data_raw == "[DONE]":
                        break
                    try:
                        resp_data = json.loads(data_raw)
                        delta = resp_data["choices"][0]["delta"]
                        if "content" in delta:
                            token = delta["content"]
                            if output_callback is not None:
                                output_callback(token)
                            else:
                                # Safe fallback: log at DEBUG — never print() to stdout
                                logger.debug("DeepSeek token: %s", token)
                            full_text += token
                    except Exception:
                        continue
            return full_text
        else:
            resp_json = resp.json()
            return resp_json["choices"][0]["message"]["content"]
    except Exception as e:
        logger.debug("DeepSeek call failed: %s", e)
        return None
