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
Phemex Automated Trading Bot
==============================
Runs the dual scanner on a schedule, picks the best setups, and auto-executes.

Strategy:
  - $10 margin per trade at 30x cross leverage ($300 notional)
  - Market order entry
  - Immediately place 0.5% trailing stop (closeOnTrigger)
  - Max 1 concurrent open position
  - Won't re-enter a symbol already in position
  - Supports both LONG and SHORT (defaults to SHORT)

Auth (from Phemex API docs):
  Headers:
    x-phemex-access-token  : API Key ID
    x-phemex-request-expiry: Unix epoch seconds (now + 60s)
    x-phemex-request-signature: HMacSha256(path + queryString + expiry + body)

Key USDT-M endpoints used:
  GET  /public/products                  — instrument lot sizes
  GET  /g-accounts/accountPositions      — balance & open positions
  PUT  /g-positions/leverage             — set leverage per symbol
  PUT  /g-orders/create                  — place order (preferred)
  GET  /g-orders/activeList              — check open/pending orders

.env keys required:
  PHEMEX_API_KEY     = your API key ID
  PHEMEX_API_SECRET  = your API secret
  PHEMEX_BASE_URL    = https://testnet-api.phemex.com   (testnet)
                       https://api.phemex.com            (mainnet)
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import argparse
import re
import datetime
import hashlib
import hmac
import json
import logging
import math
import os
import queue
import sys
import time
import threading
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Regex to strip ANSI escape codes for accurate string length calculation
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(s):
    return ANSI_ESCAPE.sub('', s)

import matplotlib
matplotlib.use('Agg')
import random
import requests
import sys
import signal
import blessed
from websocket import WebSocketApp
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from colorama import init, Fore, Style
from dotenv import load_dotenv
import core.phemex_common as pc
import core.ui as ui
import core.web_bridge as web_bridge
import modules.animations as animations
from modules.storage_manager import StorageManager

# ── Global Control ───────────────────────────────────────────────────
class BotState:
    """Thread-safe container for bot metrics to bridge with web_bridge."""
    def __init__(self):
        self.lock = threading.Lock()
        self.balance = 0.0
        self.positions = []
        self.live_prices = {}
        self.rolling_stats = {"wins": 0, "losses": 0, "win_pnl": 0.0, "loss_pnl": 0.0}
        self.scan_count = 0
        self.analyzed_count = 0
        self.last_scanner_results = []
        self.is_running = True
        self.entropy_penalty = 0.0
        self.max_positions = 8

_bot_state = BotState()
_running = True
_shutdown_requested = False
_session_wins = 0
_session_losses = 0
_session_total_pnl = 0.0
_session_equity_history = []
_equity_lock = threading.Lock()

def handle_exit(signum, frame):
    """Force an immediate exit on signal, ensuring loops are terminated."""
    global _running, _shutdown_requested
    if not _shutdown_requested:
        _shutdown_requested = True
        logger.info(f"Signal {signum} received. Forcing immediate shutdown...")
        _running = False
        sys.exit(0)

signal.signal(signal.SIGINT,  handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# ── New-module imports (graceful degradation) ────────────────────────
try:
    import modules.drawdown_guard as drawdown_guard
    _DD_OK = True
except ImportError:
    drawdown_guard = None  # type: ignore
    _DD_OK = False

try:
    import modules.risk_manager as risk_mgr
    _RM_OK = True
except ImportError:
    risk_mgr = None  # type: ignore
    _RM_OK = False

try:
    import modules.signal_analytics as analytics
    _SA_OK = True
except ImportError:
    analytics = None  # type: ignore
    _SA_OK = False

try:
    import modules.telegram_controller as telegram
    _TG_OK = True
except ImportError:
    telegram = None  # type: ignore
    _TG_OK = False

try:
    import modules.event_filter as event_filter
    _EF_OK = True
except ImportError:
    event_filter = None
    _EF_OK = False

try:
    import modules.correlation_manager as corr_mgr
    _CM_OK = True
except ImportError:
    corr_mgr = None
    _CM_OK = False

# Telegram Configuration — load exclusively from environment; never hardcode credentials
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")

# ── Configuration (moved up to avoid NameError in _initial_corr_update) ─────
MIN_VOLUME     = int(os.getenv("BOT_MIN_VOLUME", "1000000"))
RATE_LIMIT_RPS = float(os.getenv("BOT_RATE_LIMIT_RPS", "20.0"))

# ── Scanner imports ──────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Initialize unified storage for upgrade modules
p_bot_storage = StorageManager(Path(SCRIPT_DIR).parent / "data" / "state" / "fancybot.db")
if _SA_OK:
    analytics.init_storage(p_bot_storage)
if _DD_OK:
    drawdown_guard.init_storage(p_bot_storage)
if _CM_OK:
    corr_mgr.init(p_bot_storage)
if _EF_OK:
    event_filter.init(p_bot_storage)

try:
    import core.phemex_long as scanner_long
    import core.phemex_short as scanner_short
    _SCANNERS_OK = True
except ImportError as e:
    _SCANNERS_OK = False
    _SCANNER_ERR = e

load_dotenv()
init(autoreset=True)

# ── Entropy Deflator Parameters (v2) ───────────────────────────
ENTROPY_MAX_PENALTY   = int(os.getenv("ENTROPY_MAX_PENALTY", "30"))
ENTROPY_SAT_WEIGHT    = int(os.getenv("ENTROPY_SAT_WEIGHT", "20"))
ENTROPY_SAT_CAP       = int(os.getenv("ENTROPY_SAT_CAP", "20"))
ENTROPY_IMB_WEIGHT    = int(os.getenv("ENTROPY_IMB_WEIGHT", "10"))
ENTROPY_ALERT_LEVEL   = int(os.getenv("ENTROPY_ALERT_LEVEL", "20"))

# ────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────
BASE_URL       = os.getenv("PHEMEX_BASE_URL", "https://testnet-api.phemex.com")
API_KEY        = os.getenv("PHEMEX_API_KEY", "")
API_SECRET     = os.getenv("PHEMEX_API_SECRET", "")
BOT_LOG_FILE   = Path(SCRIPT_DIR).parent / "data" / "backtest_results" / "bot_trades.json"

# Entity API Configuration
ENTITY_API_KEY      = os.getenv("ENTITY_API_KEY", "")
ENTITY_API_BASE_URL = os.getenv("ENTITY_API_BASE_URL", "https://acoustic-trade-scan-now.base44.app")
ENTITY_APP_ID       = os.getenv("ENTITY_APP_ID", pc.ENTITY_APP_ID)
SESSION_ID          = f"sess-{int(time.time())}"
ENABLE_ENTITY       = bool(ENTITY_API_KEY and ENTITY_APP_ID)

def make_entity_request(entity_name: str, method: str = "POST", data: dict = None, entity_id: str = None) -> Optional[dict]:
    """
    Delegate to phemex_common.make_entity_request() so all entity calls benefit
    from the shared retry session, rate-limit compliance, and connection pooling.

    Args:
        entity_name: The endpoint or category for the request (e.g. 'signalevent').
        method: HTTP method (POST, GET, PUT).
        data: Optional payload for the request.
        entity_id: Optional ID for the entity.

    Returns:
        The JSON response as a dict, or None if the request failed or is disabled.
    """
    if not ENABLE_ENTITY:
        return None
    return pc.make_entity_request(entity_name, method=method, data=data, entity_id=entity_id)

# Strategy parameters
MARGIN_USDT    = float(os.getenv("BOT_MARGIN_USDT", "50.0"))   # $ margin per trade
LEVERAGE       = int(os.getenv("BOT_LEVERAGE", "30"))          # leverage multiplier
TRAIL_PCT      = float(os.getenv("BOT_TRAIL_PCT", "0.01"))     # 1% trailing stop
TAKE_PROFIT_PCT = float(os.getenv("BOT_TAKE_PROFIT_PCT", "0.50")) # 50% take profit
SCAN_INTERVAL  = int(os.getenv("BOT_SCAN_INTERVAL", "60"))    # seconds between scans

# Base gating thresholds
MIN_SCORE      = int(os.getenv("BOT_MIN_SCORE", "120"))
MIN_SCORE_GAP  = int(os.getenv("BOT_MIN_SCORE_GAP", "0"))
MIN_SCORE_HTF_BYPASS = int(os.getenv("BOT_MIN_SCORE_HTF", "110"))
MIN_SCORE_LOW_LIQ = int(os.getenv("BOT_MIN_SCORE_LOW_LIQ", "135"))

# Low-Liquidity Adjusted Parameters
LOW_LIQ_LEVERAGE = int(os.getenv("BOT_LOW_LIQ_LEVERAGE", "10"))
LOW_LIQ_TRAIL_PCT = float(os.getenv("BOT_LOW_LIQ_TRAIL", "0.01")) # Keep 1% for low-liq
LOW_LIQ_MARGIN = float(os.getenv("BOT_LOW_LIQ_MARGIN", "10.0"))

# Predictive score thresholds
MIN_PREDICTIVE_SCORE = float(os.getenv("BOT_MIN_PREDICTIVE_SCORE", "0.1"))
MIN_PREDICTIVE_SCORE_HTF_BYPASS = float(os.getenv("BOT_MIN_PREDICTIVE_SCORE_HTF", "0.1"))
MIN_PREDICTIVE_SCORE_LOW_LIQ = float(os.getenv("BOT_MIN_PREDICTIVE_SCORE_LOW_LIQ", "0.1"))
MIN_PREDICTIVE_SCORE_GAP = float(os.getenv("BOT_MIN_PREDICTIVE_SCORE_GAP", "0.0"))
FAST_TRACK_PREDICTIVE_SCORE = float(os.getenv("BOT_FAST_TRACK_PREDICTIVE_SCORE", "0.5"))

MAX_POSITIONS  = 999
DIRECTION      = os.getenv("BOT_DIRECTION", "BOTH")
TIMEFRAME      = os.getenv("BOT_TIMEFRAME", "1H")
MAX_WORKERS    = int(os.getenv("BOT_MAX_WORKERS", "30"))

# Position Mode: OneWay (posSide="Merged") or Hedged (posSide="Long"/"Short")
POSITION_MODE = os.getenv("BOT_POSITION_MODE", "OneWay")  # "OneWay" or "Hedged"

# ── Simulation-like features for production ─────────────────────────
_live_prices: Dict[str, float] = {}
_prices_lock = threading.Lock()
_ws_app = None
_ws_thread = None
_slot_available_event = threading.Event()
_display_paused = threading.Event()
_display_thread_running = False
_ws_connected = False # New flag to track WebSocket connection status

_animation_queue = queue.Queue()

def play_animation(anim_fn):
    """Queues a cinematic animation to be played safely."""
    _animation_queue.put(anim_fn)

def _process_animations():
    """Processes any queued animations. Should be called from a safe thread context (main loop)."""
    while not _animation_queue.empty():
        anim_fn = _animation_queue.get()
        _display_paused.set()
        time.sleep(0.5) # Let TUI finish its last frame
        animations.clear()
        try:
            anim_fn()
        except Exception as e:
            logger.error(f"Animation failed: {e}")
        finally:
            animations.clear()
            _display_paused.clear()
            _animation_queue.task_done()

_fast_track_opened: set[str] = set()
_fast_track_lock = threading.RLock()

# Local state for dashboard stop display and trade tracking
_local_stop_states: Dict[str, dict] = {}  # symbol -> {stop_price, high_water, low_water, entry_time, entry_score, direction}
# [T1-01] Dedicated lock for _local_stop_states — the WS callback thread and the
# main bot loop both mutate this dict. Without a lock there is a TOCTOU window
# between the 'symbol in _local_stop_states' guard and the subsequent dict access
# that produces an unhandled KeyError, silently killing the WS callback thread
# and freezing all trailing stops.
_stop_states_lock = threading.Lock()

FAST_TRACK_SCORE = int(os.getenv("BOT_FAST_TRACK_SCORE", str(pc.SCORE_FAST_TRACK)))
FAST_TRACK_COOLDOWN: Dict[str, float] = {}  # symbol → timestamp of last fast-track
FAST_TRACK_COOLDOWN_SECONDS = 300  # 5 minutes before same symbol can fast-track again
RESULT_STALENESS_SECONDS = 120  # discard scan results older than 2 minutes

# ── Symbol Blacklist / Cooldown ────────────────────────
# After any stop-loss exit, the symbol is banned for BLACKLIST_DURATION_SECONDS.
# Data shows 12 confirmed double-tap re-entries in the live log — this eliminates them.
SYMBOL_BLACKLIST: Dict[str, float] = {}  # symbol → blacklist expiry (epoch)
BLACKLIST_DURATION_SECONDS = int(os.getenv("BOT_BLACKLIST_SECONDS", "1800")) # 30 min (fallback)
_blacklist_lock = threading.Lock()

def get_tf_seconds(tf: str) -> int:
    """Helper to convert timeframe string to seconds."""
    mapping = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200, "1D": 86400
    }
    return mapping.get(tf, 900) # default 15m

def save_blacklist() -> None:
    """
    DEPRECATED: File-based blacklist persistence.
    Kept as a no-op shim for backward compatibility; blacklist is now stored in SQLite via StorageManager.
    """
    pass

def load_blacklist() -> None:
    """
    Loads any legacy JSON blacklist into the in-memory SYMBOL_BLACKLIST and migrates it into StorageManager.
    Subsequent blacklist persistence happens via the DB-backed StorageManager interface.
    """
    global SYMBOL_BLACKLIST
    legacy_path = Path(SCRIPT_DIR).parent / "data" / "state" / "bot_blacklist.json"
    if not legacy_path.exists():
        return

    try:
        loaded_data = json.loads(legacy_path.read_text())
    except Exception as e:
        logger.error(f"Failed to load legacy blacklist file: {e}")
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    migrated = 0
    with _blacklist_lock:
        SYMBOL_BLACKLIST = {}
        for symbol, expiry_ts in loaded_data.items():
            try:
                expiry = float(expiry_ts)
            except Exception:
                continue
            if expiry <= time.time():
                continue
            SYMBOL_BLACKLIST[symbol] = expiry
            expires_dt = datetime.datetime.fromtimestamp(expiry, datetime.timezone.utc)
            try:
                p_bot_storage.add_to_blacklist(symbol, reason="migrated", expires_at=expires_dt)
                migrated += 1
            except Exception as db_err:
                logger.error(f"Failed to migrate blacklist symbol {symbol} to DB: {db_err}")

    logger.info(f"Migrated {migrated} legacy blacklist entries into StorageManager.")

# ── Dynamic Cooldown Parameters (Project Phoenix) ───────────────────────────
# After a trade is closed, the cooldown before re-entry is dynamically calculated.
BASE_COOLDOWN_WIN_S           = int(os.getenv("BASE_COOLDOWN_WIN_S", "300"))      # 5 mins on win
BASE_COOLDOWN_LOSS_S          = int(os.getenv("BASE_COOLDOWN_LOSS_S", "1800"))     # 30 mins base on loss
PNL_COOLDOWN_MULTIPLIER       = int(os.getenv("PNL_COOLDOWN_MULTIPLIER", "72"))   # 72s per dollar lost (e.g., -$25 loss adds 30 mins)
ENTROPY_COOLDOWN_REDUCTION_F  = int(os.getenv("ENTROPY_COOLDOWN_REDUCTION_F", "120")) # 120s reduction per entropy point
MAX_COOLDOWN_S                = int(os.getenv("MAX_COOLDOWN_S", "14400"))    # 4 hour max cooldown

def _calculate_dynamic_blacklist_duration(pnl: float, entropy_penalty: int) -> int:
    """Calculates a dynamic cooldown period in seconds based on performance and market conditions."""
    if pnl >= 0:
        # Short fixed cooldown for wins, no reduction
        return BASE_COOLDOWN_WIN_S
    
    # Longer cooldown for losses, scaled by PnL
    loss_penalty = int(abs(pnl) * PNL_COOLDOWN_MULTIPLIER)
    cooldown = BASE_COOLDOWN_LOSS_S + loss_penalty

    # Reduce cooldown if market is hot (high entropy)
    reduction = entropy_penalty * ENTROPY_COOLDOWN_REDUCTION_F
    
    final_cooldown = max(0, cooldown - reduction)
    return min(final_cooldown, MAX_COOLDOWN_S)

def blacklist_symbol(symbol: str, reason: str = "stop_out", pnl: float = 0.0) -> None:
    """Add symbol to the cooldown blacklist for a dynamic duration."""
    duration = _calculate_dynamic_blacklist_duration(pnl, _entropy_penalty)
    expiry = time.time() + duration
    with _blacklist_lock:
        SYMBOL_BLACKLIST[symbol] = expiry
    msg = f"🚫 *BLACKLISTED* — {symbol} banned for {duration//60}m after {reason}"
    if duration > 0:
        logger.info(msg)
        send_telegram_message(msg)
    save_blacklist() # Save after updating blacklist

    # Entity API Hook
    # REF: Tier 3: Temporal Inconsistency
    make_entity_request("symbolblacklist", data={
        "blacklist_id": f"bl-{symbol}-{int(time.time())}",
        "symbol": symbol,
        "triggered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "expires_at": datetime.datetime.fromtimestamp(expiry, datetime.timezone.utc).isoformat(),
        "duration_seconds": duration,
        "trigger_trade_id": symbol, # best guess
        "reason": reason
    })

def is_blacklisted(symbol: str) -> bool:
    """Returns True if the symbol is currently in the cooldown period."""
    with _blacklist_lock:
        expiry = SYMBOL_BLACKLIST.get(symbol, 0)
        if time.time() < expiry:
            return True
        if expiry > 0: # expired — clean up
            del SYMBOL_BLACKLIST[symbol]
        return False

# ── Dynamic Scaling ───────────────────────────────────
# At $26 fuel, run max 2 concurrent positions to protect capital.
# As equity grows, allow more positions: $50→3, $75→4, $100+→5
SCALING_TIERS: List[Tuple[float, int]] = [
    (100.0, 5),
    (75.0,  4),
    (50.0,  3),
    (30.0,  2),
    (0.0,   1),  # survival mode below $30
]

def get_dynamic_max_positions(balance: float) -> int:
    """Return the maximum concurrent positions allowed for the given equity level."""
    actual_max = 1
    for threshold, max_pos in SCALING_TIERS:
        if balance >= threshold:
            actual_max = max_pos
            break
    return actual_max

# Account-level trailing stop
_account_high_water: float = 0.0  # peak equity seen
ACCOUNT_TRAIL_PCT = float(os.getenv("BOT_ACCOUNT_TRAIL_PCT", "0.05")) # 5% trail on peak equity
_account_trail_stop: float = 0.0  # current account stop level
_account_trading_halted: bool = False

# Cache for dashboard
_cached_balance: float = 0.0
_cached_positions: List[dict] = []
_cache_lock = threading.Lock()

def send_telegram_message(message: str) -> None:
    """Sends a message to the configured Telegram chat."""
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")

# ── Logging Setup ─────────────────────────────────────────────────────
_bot_logs = deque(maxlen=100)
_thesis_log = deque(maxlen=20)

logger = pc.setup_colored_logging(
    "phemex_bot",
    level=logging.INFO,
    log_file=Path(SCRIPT_DIR) / "bot.log",
    buffer=_bot_logs
)

def tui_log(msg: str, event_type: str = "BOT") -> None:
    """Logs a message to both the system audit log and the TUI buffer."""
    pc.log_system_event(event_type, msg)
    # Ensure it also goes into our local logger which is hooked to the TUI deque
    logger.info(msg)

# ────────────────────────────────────────────────────────────────────
# HTTP session
# ────────────────────────────────────────────────────────────────────

def build_session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    return sess

_session = build_session()

# ────────────────────────────────────────────────────────────────────
# Phemex HMAC auth
# ────────────────────────────────────────────────────────────────────

def _sign(path: str, query: str, expiry: int, body: str) -> str:
    """
    HMacSha256(URL Path + QueryString + Expiry + body)
    Exactly as documented: path + queryString (no '?') + expiry + body
    """
    message = path + query + str(expiry) + body
    # logger.info(f"Signing message: {message}") # Debug
    sig = hmac.new(
        API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    # logger.info(f"Generated signature: {sig}") # Debug
    return sig


def _auth_headers(path: str, query: str = "", body: str = "") -> dict:
    expiry = int(time.time()) + 60
    signature = _sign(path, query, expiry, body)
    headers = {
        "x-phemex-access-token": API_KEY,
        "x-phemex-request-expiry": str(expiry),
        "x-phemex-request-signature": signature,
        "Content-Type": "application/json",
    }
    # logger.info(f"Auth headers: {headers}") # Debug
    return headers


def _get(path: str, params: dict = None) -> Optional[dict]:
    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    url = BASE_URL + path + (("?" + query) if query else "")
    headers = _auth_headers(path, query)
    try:
        resp = _session.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        log_error_response(path, resp)
        return None
    except Exception as e:
        logger.error("GET %s failed: %s", path, e)
        return None


def _put(path: str, params: dict = None, body: dict = None) -> Optional[dict]:
    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    body_str = json.dumps(body) if body else ""
    url = BASE_URL + path + (("?" + query) if query else "")
    headers = _auth_headers(path, query, body_str)
    try:
        resp = _session.put(url, headers=headers, data=body_str, timeout=12)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        log_error_response(path, resp)
        return None
    except Exception as e:
        logger.error("PUT %s failed: %s", path, e)
        return None


def _post(path: str, body: dict = None) -> Optional[dict]:
    body = body or {}
    body_str = json.dumps(body)
    headers = _auth_headers(path, "", body_str)
    url = BASE_URL + path
    try:
        resp = _session.post(url, headers=headers, data=body_str, timeout=12)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        log_error_response(path, resp)
        return None
    except Exception as e:
        logger.error("POST %s failed: %s", path, e)
        return None


def _delete(path: str, params: dict = None) -> Optional[dict]:
    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    url = BASE_URL + path + (("?" + query) if query else "")
    headers = _auth_headers(path, query)
    try:
        resp = _session.delete(url, headers=headers, timeout=12)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError:
        log_error_response(path, resp)
        return None
    except Exception as e:
        logger.error("DELETE %s failed: %s", path, e)
        return None

def log_error_response(path: str, resp: requests.Response):
    """Log detailed error information from Phemex API response."""
    status_code = resp.status_code
    error_msg = ""
    exchange_code = None

    try:
        data = resp.json()
        exchange_code = data.get("code")
        phemex_msg = data.get("msg")
        phemex_data_snippet = json.dumps(data.get("data", {}))[:200]
        error_msg = f"Phemex API error for {path}: HTTP {status_code}, Phemex Code {exchange_code}, Msg: '{phemex_msg}', Data: {phemex_data_snippet}"
        logger.error(error_msg)
    except json.JSONDecodeError:
        error_msg = f"Phemex API error for {path}: HTTP {status_code}, Raw response: {resp.text[:200]}"
        logger.error(error_msg)
    except Exception as e:
        error_msg = f"Phemex API error for {path}: HTTP {status_code}, Error parsing response: {e}"
        logger.error(error_msg)

    # Entity API Hook
    # REF: Tier 3: Temporal Inconsistency
    make_entity_request("errorevent", data={
        "error_id": f"err-{int(time.time()*1000)}",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": SESSION_ID,
        "error_type": "API_ERROR",
        "severity": "CRITICAL" if status_code >= 500 else "WARNING",
        "http_status": status_code,
        "exchange_code": str(exchange_code) if exchange_code else None,
        "message": error_msg,
        "context": path
    })


# ────────────────────────────────────────────────────────────────────
# WebSocket & Live Monitoring
# ────────────────────────────────────────────────────────────────────

def _ws_on_message(ws, message):
    try:
        data = json.loads(message)
        # Handle both formats:
        # 1. Old format: {"market24h_p": {"symbol": "...", "closeRp": "..."}}
        # 2. New format: {"topic": "market24h_p", "data": [{"symbol": "...", "closeRp": "..."}]}
        ticks = []
        if "market24h_p" in data:
            ticks = [data["market24h_p"]]
        elif data.get("topic") == "market24h_p" and isinstance(data.get("data"), list):
            ticks = data["data"]

        for tick in ticks:
            symbol = tick.get("symbol")
            close_rp = tick.get("closeRp")
            if symbol and close_rp is not None:
                price = float(close_rp)
                with _prices_lock:
                    _live_prices[symbol] = price
                _check_stops_live(symbol)
                _check_account_trail()
    except Exception as e:
        logger.debug(f"WS Message error: {e}")


def _check_stops_live(symbol):
    """
    Update local trailing stop state based on price movement.
    Uses WebSocket live prices primarily, falls back to REST API if WS is down.

    [T1-01] The entire check-and-access of _local_stop_states is wrapped in
    _stop_states_lock.  The main bot loop deletes entries from this dict on
    position close; without the lock there is a TOCTOU window between the
    'symbol in' guard and the state access that produces an unhandled KeyError,
    silently killing the WS callback thread and freezing all trailing stops.
    """
    # [T1-01] Acquire lock before the membership check — holds through the state
    # access so a concurrent delete cannot land between these two operations.
    with _stop_states_lock:
        if symbol not in _local_stop_states:
            return
        # Take a shallow copy so we can release the lock before the REST fallback
        state = _local_stop_states[symbol].copy()

    current = None
    with _prices_lock:
        current = _live_prices.get(symbol)

    if current is None:  # Price not available from WS
        if not _ws_connected:  # WS is explicitly disconnected
            logger.warning(f"WS disconnected. Attempting REST API fallback for {symbol} in _check_stops_live.")
            current = _get_current_price_rest(symbol)
            if current:
                with _prices_lock:
                    _live_prices[symbol] = current
                logger.info(f"Successfully obtained REST API price for {symbol} in _check_stops_live.")
            else:
                logger.warning(f"Failed to get price for {symbol} from REST API. Cannot check stop.")
                return
        else:
            logger.debug(f"WS connected but no live price for {symbol}. Waiting for WS update.")
            return

    if not current:
        return

    direction = state["direction"]

    # [T1-01] Re-acquire lock for the in-place mutation
    with _stop_states_lock:
        if symbol not in _local_stop_states:
            return  # position was closed while we were fetching price
        if direction == "LONG":
            if current > _local_stop_states[symbol].get("high_water", 0.0):
                _local_stop_states[symbol]["high_water"] = current
                _local_stop_states[symbol]["stop_price"] = current * (1.0 - TRAIL_PCT)
        else:
            if current < _local_stop_states[symbol].get("low_water", 999999999.0):
                _local_stop_states[symbol]["low_water"] = current
                _local_stop_states[symbol]["stop_price"] = current * (1.0 + TRAIL_PCT)


def _check_account_trail():
    global _account_high_water, _account_trail_stop, _account_trading_halted
    with _cache_lock:
        balance = _cached_balance
        positions = _cached_positions
    if balance == 0 and not positions:
        return

    total_upnl = 0.0

    # 1. Identify symbols with missing prices first
    with _prices_lock:
        missing_symbols = [pos["symbol"] for pos in positions if _live_prices.get(pos["symbol"]) is None]

    # 2. Fetch missing prices outside any lock if WS is down
    if missing_symbols and not _ws_connected:
        for symbol in missing_symbols:
            logger.warning(f"WS disconnected. Attempting REST API fallback for {symbol} in _check_account_trail.")
            price = _get_current_price_rest(symbol)
            if price:
                with _prices_lock:
                    _live_prices[symbol] = price
                logger.info(f"Successfully obtained REST API price for {symbol} in _check_account_trail.")

    # 3. Calculate uPnL
    with _prices_lock:
        for pos in positions:
            sym = pos["symbol"]
            now = _live_prices.get(sym)

            if now is None:
                continue

            side = pos["side"]
            entry = pos["entry"]
            size = float(pos["size"]) # Buy = Long, Sell = Short
            upnl = (now - entry) * size if side == "Buy" else (entry - now) * size
            total_upnl += upnl

    equity = balance + total_upnl
    if _account_high_water == 0:
        _account_high_water = equity
        _account_trail_stop = equity * (1 - ACCOUNT_TRAIL_PCT)

    if equity > _account_high_water:
        _account_high_water = equity
        _account_trail_stop = equity * (1 - ACCOUNT_TRAIL_PCT)

    if not _account_trading_halted and equity < _account_trail_stop:
        _account_trading_halted = True
        msg = f"⛔ *ACCOUNT TRAIL STOP HIT* — Peak: ${ _account_high_water:.2f} Current: ${equity:.2f} Stop: ${_account_trail_stop:.2f} — trading halted"
        print(Fore.RED + Style.BRIGHT + f"\n {msg}")
        send_telegram_message(msg)
    elif _account_trading_halted and equity >= _account_trail_stop:
        _account_trading_halted = False
        msg = f"✅ *ACCOUNT RECOVERED* — Current: ${equity:.2f} (Stop: ${_account_trail_stop:.2f}) — resuming"
        print(Fore.GREEN + Style.BRIGHT + f"\n {msg}")
        send_telegram_message(msg)


def _ws_on_open(ws):
    global _ws_connected
    _ws_connected = True
    logger.info("WS Connection Opened")
    positions = get_open_positions()
    symbols = [p["symbol"] for p in positions]
    if symbols:
        sub = {"id": 1, "method": "market24h_p.subscribe", "params": symbols}
        ws.send(json.dumps(sub))

def _ws_on_error(ws, error):
    global _ws_connected
    _ws_connected = False
    logger.error(f"WS Error: {error}")

def _ws_on_close(ws, close_status_code, close_msg):
    global _ws_connected
    _ws_connected = False
    logger.warning(f"WS Connection Closed: Status Code={close_status_code}, Message={close_msg}")
    # For now, _ws_run_loop will handle reconnection after a short delay

def _ws_heartbeat(ws, stop_event):
    while not stop_event.is_set():
        time.sleep(15)
        try:
            if ws.sock and ws.sock.connected:
                ws.send(json.dumps({"id": 0, "method": "server.ping", "params": []}))
                logger.debug(f"WS Heartbeat sent. Connected: {_ws_connected}")
            else:
                logger.debug(f"WS Heartbeat skipped. Connected: {_ws_connected}")
        except Exception as e:
            logger.debug(f"WS Heartbeat error: {e}")
            break


def _ws_run_loop():
    """
    Main WebSocket execution thread.
    Handles connection lifecycle, heartbeats, and automatic reconnection with exponential backoff.
    Exits gracefully when the global _running flag is False.
    """
    global _ws_app, _ws_connected
    ws_url = "wss://ws.phemex.com"
    if "testnet" in BASE_URL:
        ws_url = "wss://testnet.phemex.com/ws"

    reconnect_delay = 1  # Start with 1 second delay
    max_reconnect_delay = 60 # Max delay of 60 seconds

    while _running:
        try:
            logger.info(f"Attempting WS connection... (current _ws_connected: {_ws_connected}, next retry in {reconnect_delay}s)")
            stop_event = threading.Event()
            _ws_app = WebSocketApp(
                ws_url,
                on_message=_ws_on_message,
                on_open=_ws_on_open,
                on_error=_ws_on_error,
                on_close=_ws_on_close
            )
            # Start heartbeat with stop signal
            threading.Thread(target=_ws_heartbeat, args=(_ws_app, stop_event), daemon=True).start()

            # This will block until connection closes or error
            _ws_app.run_forever()

            # Signal heartbeat to stop
            stop_event.set()

        except Exception as e:
            logger.error(f"WS run loop error: {e}. Reconnecting in {reconnect_delay}s...")

        # Single sleep + backoff at end of loop regardless of clean/error disconnect
        time.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)


def _ensure_ws_started():
    import traceback
    global _ws_thread
    if _ws_thread is None or not _ws_thread.is_alive():
        # REF: [Tier 1] Critical Thread Error Handling
        def _target_wrapper():
            try:
                _ws_run_loop()
            except Exception as error:
                logger.error(f"WS run loop crashed: {error}\n{traceback.format_exc()}")

        _ws_thread = threading.Thread(target=_target_wrapper, daemon=True)
        _ws_thread.start()


def _cache_refresher():
    """
    Background thread to periodically refresh account balance and open positions.
    Detects closed positions to log them and sends Telegram notifications.
    Synchronizes with the local trailing stop state.
    """
    import traceback
    global _cached_balance, _cached_positions
    while _running:
        try:
            # REF: [Tier 3] Descriptive Naming
            new_balance, new_positions = get_account_status()
            if new_balance is not None:
                with _cache_lock:
                    # Detect closure for logging
                    old_symbols = {p["symbol"] for p in _cached_positions}
                    new_symbols = {p["symbol"] for p in new_positions}

                    closed = old_symbols - new_symbols
                    if closed:
                        # Detect closure for logging
                        for sym in closed:
                            old_p = next((p for p in _cached_positions if p["symbol"] == sym), None)
                            with _stop_states_lock:  # [T1-01] guard mutation
                                local_state = _local_stop_states.pop(sym, {})

                            # Attempt to fetch the actual realized PnL from the exchange
                            realized_pnl = get_recent_realized_pnl(sym)

                            # Recover data from history if missing in cache
                            history = _read_trade_log()
                            h_entry = next((h for h in reversed(history) if h.get("symbol") == sym and h.get("status") == "entered"), None)

                            entry_price = old_p.get("entry", 0) if old_p else (h_entry.get("price", 0) if h_entry else 0)
                            qty_str = str(old_p.get("size", 0)) if old_p else (str(h_entry.get("qty", 0)) if h_entry else "0")
                            score = local_state.get("entry_predictive_score", 0.0) or (h_entry.get("predictive_score", 0.0) if h_entry else 0.0)
                            entry_time = local_state.get("entry_time") or (datetime.datetime.fromisoformat(h_entry["timestamp"]) if h_entry else datetime.datetime.now(datetime.timezone.utc))
                            # REF: Safety check for naive datetimes from history
                            if entry_time.tzinfo is None:
                                entry_time = entry_time.replace(tzinfo=datetime.timezone.utc)
                            direction = local_state.get("direction", "LONG" if (old_p and old_p["side"]=="Buy") else (h_entry.get("direction") if h_entry else "Unknown"))

                            # Standardize on timezone-aware UTC for JSON storage
                            now_utc = datetime.datetime.now(datetime.timezone.utc)
                            hold_secs = (now_utc - entry_time).total_seconds() if entry_time else 0
                            h_min, h_sec = divmod(int(hold_secs), 60)
                            h_hour, h_min = divmod(h_min, 60)
                            dur_str = f"{h_hour}h {h_min}m" if h_hour > 0 else (f"{h_min}m {h_sec}s" if h_min > 0 else f"{h_sec}s")

                            symbol_to_log = sym
                            side_to_log = old_p['side'] if old_p else ("Buy" if direction == "LONG" else "Sell")

                            msg = f"🔔 *TRADE CLOSED (Exchange Stop)* — {symbol_to_log} {side_to_log} | PnL: {realized_pnl:+.4f} | Duration: {dur_str}"

                            # Animation and Hardware signal on exit
                            if realized_pnl > 0:
                                if realized_pnl > 10.0: play_animation(animations.big_win)
                                else: play_animation(animations.win)
                            else:
                                play_animation(animations.loss)

                            send_telegram_message(msg)
                            logger.info(msg)

                            # Update session stats
                            global _session_wins, _session_losses, _session_total_pnl
                            if realized_pnl > 0:
                                _session_wins += 1
                            elif realized_pnl < 0:
                                _session_losses += 1
                            _session_total_pnl += realized_pnl

                            with _equity_lock:
                                with _cache_lock:
                                    current_equity = _cached_balance + sum(p.get("pnl", 0.0) for p in _cached_positions)
                                _session_equity_history.append(current_equity)
                            log_trade({
                                "timestamp": now_utc.isoformat(), # REF: Tier 3: Temporal Inconsistency
                                "symbol": sym,
                                "direction": direction,
                                "price": entry_price,
                                "qty": qty_str,
                                "predictive_score": score, # Updated to use predictive score
                                "status": "closed",
                                "reason": "exchange_stop",
                                "pnl": round(float(realized_pnl), 4),
                                "hold_time_seconds": int(hold_secs),
                                "signals": local_state.get("signals", []),
                                "raw_signals": local_state.get("raw_signals", {}),
                            })

                            # ── Drawdown guard — record closed trade PnL ──────
                            if _DD_OK:
                                try:
                                    drawdown_guard.record_pnl(float(realized_pnl), new_balance if new_balance is not None else _cached_balance)
                                except Exception as e:
                                    logger.warning(f"[DD] record_pnl failed: {e}")

                            # ── Signal analytics — record per-signal stats ────
                            if _SA_OK:
                                try:
                                    exit_price = old_p.get("mark_price", entry_price) if old_p else entry_price
                                    trade_signals = local_state.get("signals", [])
                                    entry_ts = local_state.get("entry_time")
                                    if entry_ts and isinstance(entry_ts, datetime.datetime):
                                        entry_ts = entry_ts.isoformat()
                                    analytics.record_trade(trade_signals, entry_price, exit_price,
                                                     float(realized_pnl), direction, sym, timestamp=entry_ts)
                                except Exception as e:
                                    logger.warning(f"[SA] record_trade failed: {e}")

                            # ── Risk manager — feed Kelly rolling stats ───────
                            if _RM_OK:
                                try:
                                    risk_mgr.record_trade_result(float(realized_pnl))
                                except Exception as e:
                                    logger.warning(f"[RM] record_trade_result failed: {e}")

                            # ── Auto-Blacklist on Closure ──────────
                            blacklist_symbol(sym, reason=f"trade closure (PnL: ${realized_pnl:.2f})", pnl=realized_pnl)

                        _slot_available_event.set()

                    # Take a local snapshot inside the lock for Entity API calls (T2-04)
                    new_positions_snapshot = list(new_positions)
                    new_balance_snapshot = new_balance

                # Entity API: Account Snapshot (uses local snapshot — race-safe)
                total_upnl = sum(p.get("pnl", 0.0) for p in new_positions_snapshot)
                equity = new_balance_snapshot + total_upnl
                drawdown = 0.0
                if _account_high_water > 0:
                    drawdown = (_account_high_water - equity) / _account_high_water * 100

                make_entity_request("accountsnapshot", data={
                    "snapshot_id": f"account-{int(time.time())}",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "trigger": "CACHE_REFRESH",
                    "balance_usdt": new_balance_snapshot,
                    "unrealised_pnl": total_upnl,
                    "equity": equity,
                    "peak_equity": _account_high_water,
                    "account_trail_stop": _account_trail_stop,
                    "drawdown_from_peak_pct": drawdown,
                    "trading_halted": _account_trading_halted,
                    "open_positions": len(new_positions_snapshot),
                    "max_positions_allowed": get_dynamic_max_positions(new_balance_snapshot)
                })

                # Entity API: Positions
                for pos in new_positions_snapshot:
                    make_entity_request("position", data={
                        "position_id": f"pos-{pos['symbol']}-{int(time.time())}",
                        "symbol": pos["symbol"],
                        "side": pos["side"],
                        "size": pos["size"],
                        "entry_price": pos["entry"],
                        "unrealised_pnl": pos.get("pnl", 0.0),
                        "leverage": LEVERAGE, # best guess
                        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    })

                with _cache_lock:
                    _cached_balance = new_balance
                    _cached_positions = new_positions
        except Exception as error:
            # REF: [Tier 1] Critical Thread Error Handling
            logger.error(f"Cache refresh error: {error}\n{traceback.format_exc()}")
        time.sleep(30)


def _subscribe_symbol(symbol):
    def _do_sub():
        import traceback
        try:
            time.sleep(2)
            if _ws_app and _ws_app.sock and _ws_app.sock.connected:
                sub = {"id": 1, "method": "market24h_p.subscribe", "params": [symbol]}
                _ws_app.send(json.dumps(sub))
        except Exception as e:
            logger.error(f"Subscription thread failed for {symbol}: {e}\n{traceback.format_exc()}")
    threading.Thread(target=_do_sub, daemon=True).start()


_pnl_histories: Dict[str, List[float]] = {}
def _update_pnl_history(symbol: str, current_upnl: float):
    if symbol not in _pnl_histories:
        _pnl_histories[symbol] = []
    _pnl_histories[symbol].append(current_upnl)
    if len(_pnl_histories[symbol]) > 200:
        _pnl_histories[symbol].pop(0)

def _get_tui_logs() -> str:
    """Returns the last 15 lines of system logs as a single string."""
    return "\n".join(list(_bot_logs)[-15:])

def _get_session_chart() -> Optional[str]:
    """Generates a PnL chart using matplotlib and returns the file path."""
    try:
        import matplotlib.pyplot as plt
        import os

        with _equity_lock:
            data = list(_session_equity_history)

        if not data:
            # If no trades yet, use initial balance
            data = [_cached_balance] if _cached_balance > 0 else [100.0]

        plt.figure(figsize=(10, 5))
        plt.plot(data, marker='o', linestyle='-', color='b')
        plt.title(f"Session Equity Curve ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})")
        plt.xlabel("Trade Count")
        plt.ylabel("Equity (USDT)")
        plt.grid(True)

        logs_dir = Path(SCRIPT_DIR).parent / "data" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        chart_path = logs_dir / f"session_chart_{int(time.time())}.png"
        plt.savefig(chart_path)
        plt.close()

        return str(chart_path)
    except Exception as e:
        logger.error(f"Failed to generate chart: {e}")
        return None

def _run_manual_backtest(text: str) -> str:
    """Parses backtest command and runs a mini backtest."""
    import research.backtest as bt

    parts = text.split()
    # /backtest [symbol] [timeframe] [candles]
    symbol = parts[1].upper() if len(parts) > 1 else "BTCUSDT"
    tf = parts[2] if len(parts) > 2 else TIMEFRAME
    candles = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 300

    try:
        # Fetch data for backtest
        ohlc_rows = bt.get_candles(symbol, timeframe=tf, limit=candles)
        spread = bt.get_spread_pct(symbol)
        funding = bt.get_funding(symbol)
        rsi_1h = bt.get_htf_rsi(symbol)

        if not ohlc_rows or len(ohlc_rows) < 110:
            return f"❌ Insufficient data for {symbol} ({len(ohlc_rows)} candles)"

        trades = bt.backtest_symbol(
            symbol, ohlc_rows, spread, funding, rsi_1h,
            min_score=MIN_SCORE, trail_pct=TRAIL_PCT, leverage=LEVERAGE,
            margin=MARGIN_USDT, max_margin=150.0,
            direction=DIRECTION
        )

        if not trades:
            return f"No trades triggered for {symbol} ({tf}, {candles} candles)."

        # Format brief report
        win_trades = [t for t in trades if t.pnl_usdt > 0]
        total_pnl = sum(t.pnl_usdt for t in trades)

        report = [
            f"🧪 *Backtest Results: {symbol}*",
            f"Period: `{candles}` candles (`{tf}`)",
            f"Trades: `{len(trades)}`",
            f"Win Rate: `{len(win_trades)/len(trades)*100:.1f}%`",
            f"Total PnL: `{total_pnl:+.4f} USDT`",
            "",
            "Recent Trades:"
        ]

        for t in trades[-5:]:
            emoji = "✅" if t.pnl_usdt > 0 else "❌"
            report.append(f"{emoji} {t.direction} | PnL: `{t.pnl_usdt:+.2f}`")

        return "\n".join(report)
    except Exception as e:
        logger.error(f"Backtest callback error: {e}")
        return f"Error: {e}"


def _manual_tg_scan() -> str:
    """Triggers a manual dual-direction scan and returns a formatted report for Telegram."""
    # Use current bot config
    cfg = {
        "MIN_VOLUME": MIN_VOLUME,
        "TIMEFRAME":  TIMEFRAME,
        "CANDLES":    500,
        "TOP_N":      5,
        "MIN_SCORE":  0,
        "MAX_WORKERS": MAX_WORKERS,
        "RATE_LIMIT_RPS": RATE_LIMIT_RPS,
    }
    # Create a dummy args for scanner
    class DummyArgs:
        no_ai = True
        no_entity = True

    try:
        long_r, short_r = run_scanner_both(cfg, DummyArgs())

        # Format a brief report
        lines = [f"🔍 *Manual Scan ({TIMEFRAME})*"]

        tagged_long  = [dict(r, _dir="LONG")  for r in long_r]
        tagged_short = [dict(r, _dir="SHORT") for r in short_r]
        combined = sorted(tagged_long + tagged_short, key=lambda x: x["score"], reverse=True)

        top = combined[:8]
        if not top:
            lines.append("No instruments found matching volume criteria.")
        else:
            for r in top:
                direction = r.get("_dir", "?")
                arrow = "▲" if direction == "LONG" else "▼"
                lines.append(f"{arrow} `{r['inst_id']}` | Score: `{r['score']}` | Price: `{r['price']:.5g}`")

        return "\n".join(lines)
    except Exception as e:
        return f"Scan failed: {e}"


def _live_pnl_display():
    """Full-screen TUI dashboard using blessed."""
    term = blessed.Terminal()
    global _display_thread_running

    def draw_panel(x: int, y: int, panel_text: str):
        """Helper to print multi-line panels at specific coordinates line-by-line."""
        for i, line in enumerate(panel_text.split("\n")):
            print(term.move_xy(x, y + i) + line)

    with term.fullscreen(), term.hidden_cursor():
        while _display_thread_running:
            if _display_paused.is_set():
                time.sleep(1)
                continue

            with _cache_lock:
                balance = _cached_balance
                positions = _cached_positions[:]

            all_trades = _read_trade_log()
            closed_trades = [t for t in all_trades if t.get("status") == "closed" or "pnl" in t and t["pnl"] != 0]

            print(term.clear)

            # --- Header ---
            curr_time = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
            pulse = "●" if int(time.time()) % 2 == 0 else " "
            banner_lines = pc.BANNER.split("\n")
            for i, line in enumerate(banner_lines):
                print(term.move_xy(2, i+1) + ui.gradient_text(line, (0, 255, 255), (255, 0, 255)))

            header_y = len(banner_lines) + 1
            print(term.move_xy(2, header_y) + ui.hr_double(Fore.MAGENTA))
            print(term.move_xy(2, header_y + 1) + term.bold_white(f" {Fore.MAGENTA}{pulse}{Style.RESET_ALL} LIVE PRODUCTION DASHBOARD | {curr_time} UTC"))

            # --- Layout Constants ---
            max_w = term.width - 4
            left_w = int(max_w * 0.65)
            right_w = max_w - left_w - 2
            start_y = header_y + 3

            # --- Left Column: Wallet & Positions ---
            y = start_y

            # 1. Account Summary Panel
            total_upnl = 0.0
            for pos in positions:
                sym = pos["symbol"]
                with _prices_lock:
                    now = _live_prices.get(sym)
                    if now:
                        upnl = (now - pos['entry']) * pos['size'] if pos['side'] == "Buy" else (pos['entry'] - now) * pos['size']
                        total_upnl += upnl
                        _update_pnl_history(sym, upnl)

            equity = balance + total_upnl
            halt_status = f" {Fore.RED}[HALTED]{Style.RESET_ALL}" if _account_trading_halted else ""
            summary_lines = [
                ui.cyber_telemetry("Wallet", balance, 1000.0, "$"), # Target is arbitrary for viz
                ui.cyber_telemetry("uPnL", total_upnl, 100.0, "$"),
                f" Equity: {Style.BRIGHT}{equity:.2f} USDT{Style.RESET_ALL}{halt_status}",
                f" Peak:   ${_account_high_water:.2f} | Stop: ${_account_trail_stop:.2f}",
                f" Max Positions: {get_dynamic_max_positions(balance)}"
            ]

            # Blacklist/Cooldowns
            with _blacklist_lock:
                bl_active = {s: exp for s, exp in SYMBOL_BLACKLIST.items() if time.time() < exp}
                if bl_active:
                    bl_str = ", ".join(f"{s}({int((e-time.time())/60)}m)" for s, e in bl_active.items())
                    summary_lines.append(f" {Fore.YELLOW}🚫 COOLDOWN: {bl_str[:left_w-15]}{Style.RESET_ALL}")

            draw_panel(2, y, ui.glow_panel("SYSTEM CORE", summary_lines, color_rgb=(0, 255, 255), width=left_w))
            y += len(summary_lines) + 3

            # 2. Active Positions Panel
            # Calculate how many positions we can show
            remaining_h = (term.height - 2) - y - 10 # 10 lines for logs and footer
            pos_h = 9 # Height of a position card + gap
            max_show = max(1, remaining_h // pos_h)

            pos_y = y
            if not positions:
                draw_panel(2, pos_y, ui.modern_panel("ACTIVE POSITIONS", [Fore.WHITE + " (Awaiting entry signals...)"], width=left_w))
                y += 4
            else:
                for pos in positions[:max_show]:
                    sym = pos["symbol"]
                    with _prices_lock:
                        now = _live_prices.get(sym)

                    if not now:
                        draw_panel(2, y, ui.modern_panel(sym, [Fore.WHITE + "Waiting for price..."], width=left_w))
                        y += 4; continue

                    side = pos["side"]
                    upnl = (now - pos['entry']) * pos['size'] if side == "Buy" else (pos['entry'] - now) * pos['size']

                    # Mini chart and stats
                    hist = _pnl_histories.get(sym, [0.0])
                    chart_lines = ui.render_pnl_chart(hist, width=left_w-20, height=3)

                    pnl_str = f"{ui.pnl_color(upnl)}{upnl:+.4f} USDT{Style.RESET_ALL}"
                    dir_badge = f"{Fore.GREEN}▲ LONG{Style.RESET_ALL}" if side == "Buy" else f"{Fore.RED}▼ SHORT{Style.RESET_ALL}"

                    # Duration
                    dur_str = ""
                    with _stop_states_lock:
                        ls = _local_stop_states.get(sym, {})
                    et = ls.get("entry_time")
                    if et:
                        if et.tzinfo is None: et = et.replace(tzinfo=datetime.timezone.utc)
                        diff = datetime.datetime.now(datetime.timezone.utc) - et
                        tot = int(diff.total_seconds())
                        dur_str = f"({tot//60}m {tot%60}s)"

                    # Calculate stop distance percentage
                    with _stop_states_lock:
                        ls = _local_stop_states.get(sym, {})
                    stop_px = ls.get("stop_price", pos['entry'])
                    total_range = abs(pos['entry'] - stop_px) or 1e-10
                    dist_to_stop = abs(now - stop_px)
                    stop_pct = (dist_to_stop / total_range) * 100
                    stop_bar = ui.braille_progress_bar(stop_pct, width=15)

                    pos_header = f"{dir_badge} {term.bold_white(sym)} {pnl_str} {dur_str}"
                    pos_info = [
                        f" Entry: {pos['entry']:.5g} | Now: {now:.5g} | Stop Guard: [{stop_bar}]",
                        f" {chart_lines[0]}",
                        f" {chart_lines[1]}",
                        f" {chart_lines[2]}"
                    ]
                    draw_panel(2, y, ui.glow_panel(pos_header, pos_info, width=left_w, color_rgb=(255, 0, 255)))
                    y += len(pos_info) + 2

                if len(positions) > max_show:
                    print(term.move_xy(2, y) + term.italic_white(f"  ... and {len(positions) - max_show} more positions hidden"))
                    y += 2

            # 3. System Logs
            log_y = y
            log_lines = list(_bot_logs)[-5:]
            while len(log_lines) < 5: log_lines.insert(0, "")
            draw_panel(2, log_y, ui.modern_panel("SYSTEM LOGS", log_lines, width=left_w, color=Fore.WHITE))

            # 4. Machine Thesis & Sector Momentum
            thesis_y = log_y + 7
            thesis_lines = list(_thesis_log)[-5:]
            while len(thesis_lines) < 5: thesis_lines.append("")

            from modules.sector_manager import sector_manager
            sector_scores = sector_manager.get_all_sector_scores()
            sector_viz = []
            for s, s_score in sector_scores.items():
                if s_score > 0:
                    bar = ui.braille_progress_bar(min(100, s_score / 1.5), width=10)
                    sector_viz.append(f" {s:<6} [{bar}] {s_score:>5.1f}")

            print(term.move_xy(2, thesis_y) + ui.glow_panel("MACHINE THESIS", thesis_lines, color_rgb=(255, 255, 0), width=left_w))

            radar_y = thesis_y + 8
            if sector_viz:
                draw_panel(2, radar_y, ui.modern_panel("SECTOR MOMENTUM", sector_viz[:4], width=left_w, color=Fore.YELLOW))

            # --- Right Column: Trade History ---
            hist_y = start_y
            hist_lines = []

            # Stats Summary at top of history
            if closed_trades:
                wins = len([t for t in closed_trades if t.get("pnl", 0) > 0])
                wr = (wins / len(closed_trades) * 100)
                tot_pnl = sum(t.get("pnl", 0) for t in closed_trades)
                hist_lines.append(f"{Fore.WHITE}Wins: {Fore.GREEN}{wins}{Style.RESET_ALL} | Loss: {Fore.RED}{len(closed_trades)-wins}{Style.RESET_ALL} | WR: {wr:.1f}%")
                hist_lines.append(f"Total PnL: {ui.pnl_color(tot_pnl)}{tot_pnl:+.2f} USDT{Style.RESET_ALL}")
                hist_lines.append(ui.hr_dash())

            # Recent trades
            for t in reversed(closed_trades[-15:]):
                p = t.get('pnl', 0)
                ts = t['timestamp'][11:16]
                sym = t['symbol'].replace('USDT', '')
                hist_lines.append(f" {ts} {sym:<6} {ui.pnl_color(p)}{p:+.2f}{Style.RESET_ALL}")

            draw_panel(left_w + 4, hist_y, ui.modern_panel("TRADE HISTORY", hist_lines, width=right_w, color=Fore.CYAN))

            sys.stdout.flush()
            time.sleep(1)


# ────────────────────────────────────────────────────────────────────
# Instrument info cache (lot sizes)
# ────────────────────────────────────────────────────────────────────
_instrument_cache: Dict[str, dict] = {}
_instrument_loaded = False


def _load_instruments():
    global _instrument_loaded
    if _instrument_loaded:
        return
    data = _get("/public/products")
    if not data or data.get("code") != 0:
        logger.warning("Could not load instrument data — will use fallback qty rounding")
        _instrument_loaded = True
        return
    for prod in (data.get("data", {}).get("perpProductsV2") or []):
        sym = prod.get("symbol")
        if not sym:
            continue
        # qtyStepSize for lot sizing
        step_str = (
            prod.get("qtyStepSize") or
            prod.get("qtyStepSizeRq") or
            "0.001"
        )
        try:
            step = float(step_str)
        except Exception:
            step = 0.001
        _instrument_cache[sym] = {"step": step}
    _instrument_loaded = True
    logger.info("Loaded %d instrument specs", len(_instrument_cache))


def _round_qty(symbol: str, qty: float) -> str:
    """
    Round qty down to the instrument's lot step size.
    Falls back to 3 decimal places if instrument data unavailable.
    """
    _load_instruments()
    info = _instrument_cache.get(symbol)
    if info:
        step = info["step"]
        if step <= 0:
            step = 0.001
        rounded = math.floor(qty / step) * step
        # Determine decimal places from step
        if step >= 1:
            decimals = 0
        else:
            decimals = len(str(step).rstrip("0").split(".")[-1])
        return f"{rounded:.{decimals}f}"
    else:
        # Fallback: use 3 decimal places for most coins
        return f"{math.floor(qty * 1000) / 1000:.3f}"

def _get_current_price_rest(symbol: str) -> Optional[float]:
    """Fetches the current price of a symbol using the REST API."""
    path = "/md/v2/kline/list"
    params = {
        "symbol": symbol,
        "interval": "1m",
        "limit": 1
    }
    data = _get(path, params)
    if data and data.get("code") == 0 and data.get("data") and data["data"]["rows"]:
        try:
            # Phemex kline: [ts, interval, last_close, open, high, low, close, volume, turnover]
            # Close is at index 6
            return float(data["data"]["rows"][0][6])
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing REST API price for {symbol}: {e}")
    logger.debug(f"Could not get REST API price for {symbol}. Response: {data}")
    return None

# ────────────────────────────────────────────────────────────────────
# Account & position queries
# ────────────────────────────────────────────────────────────────────

def get_account_status() -> Tuple[Optional[float], List[dict]]:
    """Fetches both balance and open positions in a single API call."""
    data = _get("/g-accounts/accountPositions", {"currency": "USDT"})
    if not data or data.get("code") != 0:
        return None, []

    # Parse Balance
    balance = None
    try:
        bal_str = data["data"]["account"]["accountBalanceRv"]
        balance = float(bal_str)
    except Exception as e:
        logger.error("Balance parse error: %s", e)

    # Parse Positions
    positions = []
    for pos in (data.get("data", {}).get("positions") or []):
        try:
            size = float(pos.get("size") or "0")
        except Exception:
            size = 0.0
        if size == 0.0:
            continue

        positions.append({
            "symbol": pos.get("symbol"),
            "side": pos.get("side"),  # "Buy" or "Sell"
            "size": size,
            "entry": float(pos.get("avgEntryPriceRp") or 0.0),
            "pnl": float(pos.get("unrealisedPnlRv") or 0.0),
            "pos_side": pos.get("posSide", "Merged"),
        })

    return balance, positions


def get_balance() -> Optional[float]:
    """Returns available USDT balance."""
    balance, _ = get_account_status()
    return balance


def get_open_positions() -> List[dict]:
    """
    Returns list of USDT-M positions that are actually open (size != 0).
    Each dict has: symbol, side (Buy/Sell), size (float), avgEntryPriceRp
    """
    _, positions = get_account_status()
    return positions


def get_recent_realized_pnl(symbol: str) -> float:
    """Fetch the realized PnL of the most recent closed trade for a symbol."""
    # This uses the Phemex Contract/Unified account data API
    # type=1 (REALIZED_PNL)
    params = {
        "currency": "USDT",
        "type": "1",
        "limit": "5",
    }
    data = _get("/api-data/futures/v2/tradeAccountDetail", params)
    if not data or data.get("code") != 0:
        return 0.0

    # The response 'data' field is actually a list, not a dict with 'rows'
    items = data.get("data", [])
    if not isinstance(items, list) or not items:
        return 0.0

    # If the API provides a symbol, use it to filter.
    # Otherwise, we'll take the most recent amount if it happened very recently (last 60s)
    # as a best-effort guess, but only if it's likely our trade.
    for item in items:
        item_symbol = item.get("symbol")
        ts = item.get("createTime", 0) / 1000

        # If symbol matches, we're sure
        if item_symbol == symbol:
            return float(item.get("amountRv") or 0.0)

        # If symbol is missing but it's very recent, take the first one
        # (This preserves the original 'best-effort' behavior but adds symbol check)
        if not item_symbol and time.time() - ts < 60:
            return float(item.get("amountRv") or 0.0)

    return 0.0


def symbols_in_position() -> set:
    return {p["symbol"] for p in get_open_positions()}


# ────────────────────────────────────────────────────────────────────
# Leverage setter
# ────────────────────────────────────────────────────────────────────

def set_leverage(symbol: str, leverage: int, pos_side: Optional[str] = None) -> bool:
    """
    Set leverage for a symbol before entry.
    Uses cross margin (leverageRr = leverage).
    For cross margin mode in Phemex USDT perps, pass positive leverage.
    The exchange's margin mode (cross vs isolated) is set in the account settings;
    this call only sets the leverage multiplier. If the requested leverage fails
    due to TE_ERR_INVALID_LEVERAGE, it attempts to set a lower default.
    """
    if pos_side is None:
        pos_side = "Merged" if POSITION_MODE == "OneWay" else "Long"

    # Attempt to set the requested leverage
    result = _put("/g-positions/leverage", params={
        "symbol": symbol,
        "leverageRr": str(leverage),
        "posSide": pos_side,
    })
    if result and result.get("code") == 0:
        logger.debug("Leverage set to %dx for %s", leverage, symbol)
        return True

    # If initial attempt fails with invalid leverage, try a lower value
    if isinstance(result, dict) and result.get("code") == 20003:
        fallback_leverage = 10  # Try 10x as a fallback
        logger.warning(f"TE_ERR_INVALID_LEVERAGE for {symbol} at {leverage}x. Retrying with {fallback_leverage}x.")

        result_fallback = _put("/g-positions/leverage", params={
            "symbol": symbol,
            "leverageRr": str(fallback_leverage),
            "posSide": pos_side,
        })
        if result_fallback and result_fallback.get("code") == 0:
            logger.info(f"Leverage successfully set to {fallback_leverage}x for {symbol} after fallback.")
            return True
        else:
            logger.error(f"Fallback leverage to {fallback_leverage}x also failed for {symbol}: {result_fallback}")
            return False

    logger.warning("Failed to set leverage for %s to %dx: %s", symbol, leverage, result)
    return False


def _switch_pos_mode(symbol: str, target_mode: str) -> bool:
    """
    Ensures the position mode for a symbol is set correctly.
    target_mode: 'BothSide' (Hedged) or 'MergedSingle' (One-Way)
    """
    path = "/g-positions/switch-pos-mode-sync"
    params = {
        "symbol": symbol,
        "targetPosMode": target_mode
    }
    result = _put(path, params=params)
    if result and result.get("code") == 0:
        logger.debug("Position mode for %s set to %s", symbol, target_mode)
        return True

    # If already in the correct mode, Phemex might return an error code like 20002 or 20004
    # We should handle that gracefully.
    if isinstance(result, dict) and result.get("code") in [20002, 20004, 34002, 10500]:
        # Note: Some APIs return 10500 if already in that mode, though 10500 is often 'invalid targetPosMode'
        # We'll rely on our updated target_mode values being correct.
        return True

    if isinstance(result, dict) and result.get("code") is not None:
        logger.error(f"Failed to set position mode for {symbol} to {target_mode}: {result.get('msg')} (Code {result.get('code')})")
    elif result is not None:
        logger.error("Failed to set position mode for %s to %s: Unexpected response format: %s", symbol, target_mode, result)
    else:
        logger.error("Failed to set position mode for %s to %s: No response from API", symbol, target_mode)
    return False


# ────────────────────────────────────────────────────────────────────
# Order placement
# ────────────────────────────────────────────────────────────────────

def _clord_id(prefix: str = "bot") -> str:
    ts = int(time.time() * 1000) % 1_000_000_000
    suffix = random.randint(1000, 9999)
    return f"{prefix}-{ts}-{suffix}"


def place_market_order(
    symbol: str,
    side: str,         # "Buy" or "Sell"
    qty_str: str,      # real quantity string e.g. "0.003"
    pos_side: Optional[str] = None,
    clord_id: Optional[str] = None, # New argument for client order ID
) -> Optional[dict]:
    """
    Place a market order on USDT-M perpetuals.
    Uses PUT /g-orders/create (preferred endpoint per docs).
    """
    clord = clord_id if clord_id else _clord_id("entry")
    if pos_side is None:
        if POSITION_MODE == "OneWay":
            pos_side = "Merged"
        else:
            pos_side = "Long" if side == "Buy" else "Short"

    params = {
        "clOrdID": clord,
        "symbol": symbol,
        "side": side,
        "ordType": "Market",
        "orderQtyRq": qty_str,
        "posSide": pos_side,
        "timeInForce": "ImmediateOrCancel",
    }
    result = _put("/g-orders/create", params=params)
    return result


def place_trailing_stop(
    symbol: str,
    side: str,          # "Sell" for long, "Buy" for short
    qty_str: str,
    price: float,       # entry/current price for initial stop calculation
    trail_pct: float = 0.005,
    pos_side: Optional[str] = None,
) -> Optional[dict]:
    """
    Place a trailing stop order that closes the entire position.
    From Phemex docs (USDT-M section):
        ordType = "Stop"
        pegPriceType = "TrailingStopPeg"
        pegOffsetValueRp: negative for long (Sell), positive for short (Buy)
        stopPxRp : initial trigger price
            long:  price * (1 - trail_pct) — must be < last price
            short: price * (1 + trail_pct) — must be > last price
        closeOnTrigger = true
        orderQtyRq = "0" (close entire position)
        triggerType = "ByLastPrice"
        timeInForce = "GoodTillCancel"
    """
    if pos_side is None:
        if POSITION_MODE == "OneWay":
            pos_side = "Merged"
        else:
            # For Hedged mode, if we are closing a Long position (side=Sell), posSide must be Long
            # If we are closing a Short position (side=Buy), posSide must be Short
            pos_side = "Long" if side == "Sell" else "Short"

    offset_amount = price * trail_pct

    if side == "Sell": # Closing a long position
        stop_px = price * (1.0 - trail_pct)
        peg_offset = f"{-offset_amount:.4f}" # negative = trail below for long
    else: # Closing a short position
        stop_px = price * (1.0 + trail_pct)
        peg_offset = f"{offset_amount:.4f}"  # positive = trail above for short

    clord = _clord_id("trail")
    result = _put("/g-orders/create", params={
        "clOrdID": clord,
        "symbol": symbol,
        "side": side,
        "ordType": "Stop",
        "orderQtyRq": qty_str,
        "stopPxRp": f"{stop_px:.4f}",
        "pegPriceType": "TrailingStopPeg",
        "pegOffsetValueRp": peg_offset,
        "triggerType": "ByLastPrice",
        "timeInForce": "GoodTillCancel",
        "closeOnTrigger": "true",
        "posSide": pos_side,
    })

    if result:
        logger.info(f"[TRAIL STOP] {symbol} response: code={result.get('code')} data={json.dumps(result.get('data', {}))[:200]}")
    else:
        logger.warning(f"[TRAIL STOP] {symbol} — no response from API")
    return result


# ────────────────────────────────────────────────────────────────────
# Cancel existing trailing stops (before re-placing)
# ────────────────────────────────────────────────────────────────────

def cancel_all_orders(symbol: str) -> bool:
    """Cancel all active + untriggered orders for a symbol."""
    # REF: Tier 3: Non-Descriptive Variable Naming (r1, r2 -> active_order_resp, conditional_order_resp)
    # Cancel active orders
    active_order_resp = _delete(
        "/g-orders/all",
        params={"symbol": symbol, "untriggered": "false"},
    )
    # Cancel untriggered conditional orders (trailing stops that haven't fired)
    conditional_order_resp = _delete(
        "/g-orders/all",
        params={"symbol": symbol, "untriggered": "true"},
    )
    ok1 = active_order_resp and active_order_resp.get("code") == 0
    ok2 = conditional_order_resp and conditional_order_resp.get("code") == 0
    return ok1 or ok2

def cancel_order_by_client_id(symbol: str, client_order_id: str) -> bool:
    """Cancel a specific order using its client order ID."""
    result = _delete("/g-orders/cancel", params={"symbol": symbol, "clOrdID": client_order_id})
    if result and result.get("code") == 0:
        logger.info(f"Successfully cancelled order {client_order_id} for {symbol}.")
        return True
    else:
        logger.error(f"Failed to cancel order {client_order_id} for {symbol}: {result}")
        return False


# ────────────────────────────────────────────────────────────────────
# Trade logging
# ────────────────────────────────────────────────────────────────────
_log_lock = threading.Lock()


def log_trade(entry: dict):
    """Append trade entry to the JSON Lines log (O(1) per write).
    All readers (dashboard, history recovery) use the .jsonl file.
    """
    with _log_lock:
        log_file_jsonl = BOT_LOG_FILE.with_suffix(".jsonl")
        try:
            with open(log_file_jsonl, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to append to trade log: {e}")

    # ── SQLite History Integration (Upgrade #9) ──────────────────────
    if entry.get("status") == "closed":
        try:
            # Map p_bot entry to StorageManager record
            record = {
                "symbol":      entry.get("symbol"),
                "direction":   entry.get("direction"),
                "entry":       entry.get("price", 0.0),
                "exit":        entry.get("exit_price", entry.get("price", 0.0)),
                "pnl":         entry.get("pnl", 0.0),
                "hold_time_s": entry.get("hold_time_seconds"),
                "score":       entry.get("score"),
                "reason":      entry.get("reason"),
                "timestamp":   entry.get("timestamp"),
                "signals":     entry.get("signals", []),
                "slippage":    entry.get("slippage", 0.0),
                "raw_signals": entry.get("raw_signals", {}),
            }
            p_bot_storage.append_trade(record)
        except Exception as e:
            logger.error(f"Failed to append trade to SQLite: {e}")


def _read_trade_log() -> list:
    """Read all trade entries from the JSON Lines log file. O(n) but called rarely."""
    log_file_jsonl = BOT_LOG_FILE.with_suffix(".jsonl")
    trades = []
    with _log_lock:
        if log_file_jsonl.exists():
            try:
                with open(log_file_jsonl) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                trades.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except Exception as e:
                logger.warning(f"Failed to read trade log: {e}")
        elif BOT_LOG_FILE.exists():
            # Migrate legacy .json list to .jsonl on first read
            try:
                old = json.loads(BOT_LOG_FILE.read_text())
                if isinstance(old, list):
                    trades = old
                    with open(log_file_jsonl, "a") as f:
                        for t in old:
                            f.write(json.dumps(t) + "\n")
                    logger.info(f"Migrated {len(old)} legacy trades to {log_file_jsonl}")
            except Exception as e:
                logger.warning(f"Legacy trade log migration failed: {e}")
    return trades
def verify_candidate(symbol: str, direction: str, original_score: int, wait_seconds: int = 30) -> Optional[dict]:
    """
    Iterative 3-step verification over wait_seconds to confirm signal validity.
    Checks price stability and score consistency before execution.
    """
    steps = 3
    step_wait = wait_seconds / steps
    initial_price = None
    last_result = None

    tui_log(f"VERIFY: {symbol} ({direction}) for {wait_seconds}s...", event_type="WAIT")

    for i in range(steps):
        time.sleep(step_wait)

        # Fetch fresh ticker
        try:
            tickers = pc.get_tickers(rps=RATE_LIMIT_RPS)
            ticker = next((t for t in tickers if t["symbol"] == symbol), None)
        except Exception as e:
            tui_log(f"FAIL: {symbol} ticker fetch error: {e}", event_type="FAIL")
            return None

        if not ticker:
            tui_log(f"FAIL: {symbol} ticker not found during verification.", event_type="FAIL")
            return None

        current_price = float(ticker.get("lastRp") or ticker.get("closeRp") or 0.0)
        if initial_price is None:
            initial_price = current_price

        # Price movement check (avoid entries moving too fast against us)
        price_change = pc.pct_change(current_price, initial_price)
        if direction == "LONG" and price_change < -0.8:
            tui_log(f"FAIL: {symbol} dropping during verify: {price_change:+.2f}%", event_type="FAIL")
            return None
        elif direction == "SHORT" and price_change > 0.8:
            tui_log(f"FAIL: {symbol} pumping during verify: {price_change:+.2f}%", event_type="FAIL")
            return None

        # Re-scan using the appropriate scanner module
        scanner = scanner_long if direction == "LONG" else scanner_short
        cfg = {
            "TIMEFRAME": TIMEFRAME,
            "MIN_VOLUME": MIN_VOLUME,
            "RATE_LIMIT_RPS": RATE_LIMIT_RPS,
            "CANDLES": 500
        }

        fresh_result = scanner.analyse(ticker, cfg, enable_ai=False, enable_entity=False)
        if not fresh_result:
            tui_log(f"FAIL: {symbol} no longer qualifies at step {i+1}", event_type="FAIL")
            return None

        fresh_score = fresh_result["score"]

        # Spread check
        current_spread = fresh_result.get("spread")
        spread_ok, spread_reason = pc.check_spread_filter(current_spread, symbol)
        if not spread_ok:
            tui_log(f"FAIL: {symbol} spread too wide: {spread_reason}", event_type="FAIL")
            return None

        # Score stability check (allow 10% degradation)
        if fresh_score < original_score * 0.90:
            tui_log(f"FAIL: {symbol} score degraded: {original_score}->{fresh_score}", event_type="FAIL")
            return None

        last_result = fresh_result
        tui_log(f"  Step {i+1}/{steps}: {symbol} score {fresh_score} ({price_change:+.2f}%)", event_type="VERIFY")

    # Final overextension check
    final_change = pc.pct_change(last_result["price"], initial_price)
    if abs(final_change) > 2.0:
        tui_log(f"FAIL: {symbol} overextended ({final_change:+.2f}%) during wait.", event_type="FAIL")
        return None

    tui_log(f"VERIFIED: {symbol} ready for execution.", event_type="VERIFY")
    return last_result

def execute_setup(result: dict, direction: str, dry_run: bool = False) -> bool:
    """
    Executes a trade setup on Phemex.

    This function performs the following steps:
    1. Validates the signal and checks against the blacklist.
    2. Determines leverage and margin based on liquidity and HTF alignment.
    3. Sets the symbol's leverage on the exchange.
    4. Places a market order for entry.
    5. Places a trailing stop order for risk management.
    6. Updates local state and logs the trade.

    Args:
        result (dict): The scanner result dictionary containing 'inst_id', 'price', 'predictive_score', and 'signals'.
        direction (str): The trade direction, either 'LONG' or 'SHORT'.
        dry_run (bool): If True, logs the actions but does not place real orders.

    Returns:
        bool: True if the setup was executed successfully (or dry-run), False otherwise.
    """
    symbol = result["inst_id"]
    price  = result["price"]
    predictive_score = result.get("predictive_score", 0.0) # New predictive score
    signals = result.get("signals", [])

    if price <= 0:
        logger.warning("[%s] Invalid price %.4g — skipping", symbol, price)
        return False

    # ── Blacklist Check ────────────────────────────────
    if is_blacklisted(symbol):
        remaining = (SYMBOL_BLACKLIST.get(symbol, 0) - time.time()) / 60
        tui_log(f"SKIP: {symbol} is on cooldown ({remaining:.0f}m remaining)", event_type="SKIP")
        return False

    # ── Correlation/Overlap Gate (Idea 3) ──────────────────
    if _CM_OK:
        with _cache_lock:
            blocked, reason = corr_mgr.correlation_mgr.should_block_entry(symbol, direction, _cached_positions)
        if blocked:
            tui_log(f"CORR GATE: {symbol} blocked — {reason}", event_type="SKIP")
            return False

    # ── Liquidity-Adjusted Parameters ──────────────────
    # Low-Liquidity assets: wider stop, lower leverage, smaller margin bet.
    # Log analysis: Low-Liq appears in 45% of losses vs 22% of wins.
    is_low_liq      = any("Low Liquidity" in s for s in signals)
    is_htf_aligned  = any("HTF Alignment" in s for s in signals) # HTF alignment might still be part of signals from prediction engine

    if is_low_liq:
        active_leverage  = LOW_LIQ_LEVERAGE
        active_trail_pct = LOW_LIQ_TRAIL_PCT
        active_margin    = LOW_LIQ_MARGIN
        liq_note = f"LOW-LIQ MODE: {active_leverage}x lev, {active_trail_pct*100:.1f}% stop, ${active_margin} margin"
        tui_log(f"{symbol}: {liq_note}", event_type="LOWLIQ")
    else:
        active_leverage  = LEVERAGE
        active_trail_pct = TRAIL_PCT
        active_margin    = MARGIN_USDT

    # ── Quality Gate (using new predictive score) ───────────────────────────
    # Fine-grained gate applied at execution time.
    effective_min_predictive = MIN_PREDICTIVE_SCORE_LOW_LIQ if is_low_liq else (
        MIN_PREDICTIVE_SCORE_HTF_BYPASS if is_htf_aligned else MIN_PREDICTIVE_SCORE
    )

    if predictive_score < effective_min_predictive:
        gate_reason = "low-liq" if is_low_liq else ("htf-ok" if is_htf_aligned else "no-HTF")
        tui_log(f"SKIP: {symbol} predictive score {predictive_score:.2f} < effective min {effective_min_predictive:.2f} ({gate_reason})", event_type="SKIP")
        return False

    # ── Risk Manager sizing (Upgrade #12) ────────────────────────────
    # Overrides active_margin with Kelly/dynamic sizing when risk_manager is available.
    # Falls back to static MARGIN_USDT if module not present.
    if _RM_OK:
        try:
            stop_distance = active_trail_pct  # use trail pct as proxy for stop distance
            risk_usd, _ = risk_mgr.compute_dynamic_risk(
                account_balance=_cached_balance,
                signal_strength=min(predictive_score, 2.0), # Cap predictive score for signal strength input
                stop_distance=stop_distance,
                open_positions=_cached_positions,
                available_liquidity=result.get("depth"),
            )
            # reject_trade check
            reject, reject_reason = risk_mgr.should_reject_trade(risk_usd, _cached_balance, _cached_positions)
            if reject:
                tui_log(f"RISK MGR REJECT: {symbol} — {reject_reason}", event_type="SKIP")
                return False
            active_margin = max(risk_usd, 1.0)  # floor at $1 to avoid dust orders
            tui_log(f"RISK MGR: {symbol} dynamic margin=${active_margin:.2f} (predictive_score={predictive_score:.2f}, bal={_cached_balance:.2f})", event_type="INFO")
        except Exception as e:
            logger.warning(f"[RISK MGR] compute_dynamic_risk failed for {symbol}: {e} — using static margin")

    # ── Compute quantity ─────────────────────────────────────────────
    notional = active_margin * active_leverage
    qty_raw  = notional / price
    qty_str  = _round_qty(symbol, qty_raw)

    if float(qty_str) <= 0:
        logger.warning("[%s] Qty rounds to 0 at price %.4g — skipping", symbol, price)
        return False

    side       = "Buy"  if direction == "LONG" else "Sell"
    close_side = "Sell" if direction == "LONG" else "Buy"
    arrow      = "▲ LONG" if direction == "LONG" else "▼ SHORT"
    dir_color  = Fore.GREEN if direction == "LONG" else Fore.RED

    pos_side = "Merged" if POSITION_MODE == "OneWay" else ("Long" if direction == "LONG" else "Short")

    tui_log(f"EXECUTING {arrow} {symbol} | Predictive Score: {predictive_score:.2f} | Price: {price:.4g} | Qty: {qty_str} | Margin: ${active_margin} | Lev: {active_leverage}x", event_type="EXEC")

    # --- Entry Cinematic ---
    if predictive_score > 1.8 or result.get("score", 0) > 160:
        play_animation(animations.singularity)
    else:
        play_animation(animations.long if direction == "LONG" else animations.short)

    # --- Entity API: Trade Intent ---
    trade_id = f"tr-{symbol}-{int(time.time())}"
    make_entity_request("trade", data={
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "status": "INITIATED",
        "entry": price,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), # REF: Tier 3: Temporal Inconsistency
        "parameters": {
            "leverage": active_leverage,
            "margin": active_margin,
            "trail_pct": active_trail_pct,
            "low_liq": is_low_liq
        },
        "scoring": {
            "predictive_score": predictive_score, # Use the new predictive score
            "htf_aligned": is_htf_aligned
        },
        "market_context": {
            "htf_aligned": is_htf_aligned,
            "signals": signals[:10]
        }
    })

    if dry_run:
        tui_log(f"DRY RUN — no orders placed for {symbol}", event_type="DRY")
        # REF: Tier 3: Temporal Inconsistency
        log_trade({
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "qty": qty_str,
            "predictive_score": predictive_score, # Store predictive score
            "dry_run": True,
            "status": "dry_run",
        })
        make_entity_request("trade", method="PUT", entity_id=trade_id, data={"status": "DRY_RUN"})
        return True

    # ── Set position mode ───────────────────────────────────────────
    target_mode = "MergedSingle" if POSITION_MODE == "OneWay" else "BothSide"
    mode_ok = _switch_pos_mode(symbol, target_mode)
    if not mode_ok:
        print(Fore.RED + f" [ERROR] Failed to set position mode for {symbol} to {target_mode}. Cannot proceed.")
        return False

    # ── Set leverage ─────────────────────────────────────────────────
    lev_ok = set_leverage(symbol, active_leverage, pos_side)
    if not lev_ok:
        print(Fore.YELLOW + " [WARN] Leverage set returned non-zero — proceeding anyway (check logs for details)")

    # Generate client order ID for market entry
    entry_clord_id = _clord_id("entry")

    # ── Place market entry ───────────────────────────────────────────
    tui_log(f"Placing {side} Market order for {symbol}...", event_type="ORDER")
    order_resp = place_market_order(symbol, side, qty_str, clord_id=entry_clord_id)

    if not order_resp:
        tui_log(f"ERROR: No response from order endpoint for {symbol}", event_type="ERROR")
        make_entity_request("trade", method="PUT", entity_id=trade_id, data={"status": "ORDER_FAILED", "outcome": "NO_RESPONSE"})
        return False

    code = order_resp.get("code", -1)
    if code != 0:
        biz_err = order_resp.get("data", {}).get("bizError") if isinstance(order_resp.get("data"), dict) else None
        tui_log(f"ERROR: Order failed for {symbol}: code={code} bizError={biz_err}", event_type="ERROR")
        make_entity_request("trade", method="PUT", entity_id=trade_id, data={"status": "ORDER_FAILED", "outcome": f"CODE_{code}"})
        return False

    order_id    = order_resp.get("data", {}).get("orderID", "?")
    exec_status = order_resp.get("data", {}).get("execStatus", "?")
    avg_price   = float(order_resp.get("data", {}).get("avgPriceRp") or price)

    # Entity API Hook: Order
    # REF: Tier 3: Temporal Inconsistency
    make_entity_request("order", data={
        "order_id": order_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "type": "Market",
        "side": side,
        "qty_requested": float(qty_str),
        "qty_filled": float(order_resp.get("data", {}).get("cumQtyRq", qty_str)),
        "price_filled": avg_price,
        "status": exec_status,
        "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "leverage": active_leverage,
        "pos_side": pos_side,
        "exchange_response_code": str(code)
    })
    make_entity_request("trade", method="PUT", entity_id=trade_id, data={"status": "ENTERED", "entry": avg_price})

    tui_log(f"Entry order accepted | {symbol} | orderID: {order_id} | status: {exec_status} | Price: {avg_price:.6g}", event_type="ENTRY")

    # Telegram Alert
    emoji = "🚀" if direction == "LONG" else "📉"
    msg = (f"{emoji} *TRADE OPENED*\n\n"
           f"*Symbol:* {symbol}\n"
           f"*Direction:* {direction}\n"
           f"*Price:* {price:.4g}\n"
           f"*Predictive Score:* {predictive_score:.2f}\n" # Updated to predictive score
           f"*Time:* {datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')}")
    send_telegram_message(msg)

    # ── Brief pause to let fill propagate ───────────────────────────
    time.sleep(1.5)

    # ── Place trailing stop (with retries) ──────────────────────────
    ts_ok = False
    ts_id = None
    max_ts_retries = 3
    for i in range(max_ts_retries):
        tui_log(f"Placing trailing stop ({active_trail_pct*100:.1f}%, {close_side}) for {symbol} attempt {i+1}/{max_ts_retries}...", event_type="STOP")
        ts_resp = place_trailing_stop(symbol, close_side, qty_str, price, active_trail_pct)

        if ts_resp and ts_resp.get("code") == 0:
            ts_id = ts_resp.get("data", {}).get("orderID", "?")
            tui_log(f"Trailing stop placed | {symbol} | orderID: {ts_id}", event_type="STOP")
            ts_ok = True

            # ── Now confirmed: Add to cached positions ────────────────────────
            with _cache_lock:
                # Deduplicate: only append if not already present (prevents race with _cache_refresher)
                if not any(p["symbol"] == symbol for p in _cached_positions):
                    _cached_positions.append({
                        "symbol": symbol,
                        "side": side,
                        "size": float(qty_str),
                        "entry": avg_price,
                        "pnl": 0.0,
                        "pos_side": pos_side,
                    })
            # Entity API Hook: Order (Stop)
            # REF: Tier 3: Temporal Inconsistency
            make_entity_request("order", data={
                "order_id": ts_id,
                "trade_id": trade_id,
                "symbol": symbol,
                "type": "Stop",
                "side": close_side,
                "qty_requested": 0, # Close all
                "qty_filled": 0,
                "status": "Untriggered",
                "submitted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "leverage": active_leverage,
                "pos_side": pos_side,
                "exchange_response_code": "0"
            })
            make_entity_request("trade", method="PUT", entity_id=trade_id, data={"status": "MONITORING", "exit": 0})
            break # Exit retry loop on success
        else:
            ts_biz = ts_resp.get("data", {}).get("bizError") if isinstance(ts_resp.get("data"), dict) else None
            logger.warning(f"Trailing stop failed (attempt {i+1}): code={ts_resp.get('code')} bizError={ts_biz}. Retrying...")
            time.sleep(2) # Wait before retrying

    if not ts_ok:
        tui_log(f"CRITICAL ERROR: Failed to place trailing stop for {symbol} after {max_ts_retries} attempts.", event_type="CRITICAL")
        tui_log(f"Cancelling entry order {order_id} (clOrdID: {entry_clord_id}) to prevent unprotected position!", event_type="CRITICAL")
        make_entity_request("trade", method="PUT", entity_id=trade_id, data={"status": "STOP_FAILED_AND_CANCELLED", "outcome": "TRAIL_STOP_FAILURE"})

        # Note: We didn't append to _cached_positions yet, so no need to filter it out.

        if cancel_order_by_client_id(symbol, entry_clord_id): # entry_clord_id is the client order ID for the entry
            tui_log(f"Entry order {order_id} cancelled successfully for {symbol}.", event_type="CRITICAL")
            # REF: Tier 3: Temporal Inconsistency
            log_trade({
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "symbol": symbol,
                "direction": direction,
                "price": price,
                "qty": qty_str,
                "predictive_score": predictive_score, # Store predictive score
                "dry_run": False,
                "status": "entry_cancelled",
                "reason": "trail_stop_failed"
            })
            return False # Entry failed
        else:
            tui_log(f"CRITICAL ERROR: Failed to cancel entry order {order_id}. MANUAL INTERVENTION REQUIRED for {symbol}!", event_type="CRITICAL")
            # Send an emergency telegram message
            send_telegram_message(f"🚨 *URGENT:* Failed to place trailing stop AND failed to cancel entry for {symbol}. Manual intervention needed!")
            return False # Entry failed, and cancellation also failed

    # If trailing stop was successfully placed

    # If trailing stop was successfully placed, save local stop state for dashboard display
    offset = price * active_trail_pct
    signals = result.get("signals", []) if result else []
    raw_signals = result.get("raw_signals", {}) if result else {}
    if direction == "LONG":
        _local_stop_states[symbol] = {
            "stop_price": price - offset,
            "high_water": price,
            "entry_time": datetime.datetime.now(datetime.timezone.utc),
            "entry_predictive_score": predictive_score, # Store predictive score
            "direction": direction,
            "signals": signals,
            "raw_signals": raw_signals,
        }
    else:
        _local_stop_states[symbol] = {
            "stop_price": price + offset,
            "low_water": price,
            "entry_time": datetime.datetime.now(datetime.timezone.utc),
            "entry_predictive_score": predictive_score, # Store predictive score
            "direction": direction,
            "signals": signals,
            "raw_signals": raw_signals,
        }

    print(dir_color + Style.BRIGHT + f" {'─'*70}\n")

    # REF: Tier 3: Temporal Inconsistency
    log_trade({
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "symbol": symbol,
        "direction": direction,
        "price": price,
        "qty": qty_str,
        "notional": round(float(qty_str) * price, 2),
        "margin_usdt": active_margin,
        "leverage": active_leverage,
        "trail_pct": active_trail_pct,
        "low_liq_mode": is_low_liq,
        "htf_aligned": is_htf_aligned,
        "predictive_score": predictive_score, # Store predictive score
        "signals": result.get("signals", [])[:5],
        "entry_order_id": order_id,
        "trail_order_id": ts_id,
        "trail_ok": ts_ok,
        "dry_run": False,
        "status": "entered",
        "pnl": 0, # Entry has 0 realized PnL
    })
    return True


# ────────────────────────────────────────────────────────────────────
# Scan & decide
# ────────────────────────────────────────────────────────────────────

def run_scanner_both(cfg: dict, args, on_result=None) -> Tuple[List[dict], List[dict]]:
    """Run both scanners (no printing), return (long_results, short_results)."""
    import concurrent.futures

    def _scan(module, direction):
        return _scan_one(module, direction, cfg, args, on_result=on_result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as exe:
        fut_long  = exe.submit(_scan, scanner_long,  "LONG")
        fut_short = exe.submit(_scan, scanner_short, "SHORT")
        res_long  = fut_long.result()
        res_short = fut_short.result()

    return res_long, res_short


_print_lock = threading.Lock()

def _scan_one(module, direction: str, cfg: dict, args, on_result=None) -> List[dict]:
    # REF: [Tier 3] Descriptive Naming
    requests_per_second = cfg.get("RATE_LIMIT_RPS", 8.0)

    # Batch pre-fetch funding rates to save hundreds of API calls
    if hasattr(module, "prefetch_all_funding_rates"):
        module.prefetch_all_funding_rates(rps=requests_per_second)

    tickers = module.get_tickers(rps=requests_per_second)

    # Filter by symbols if provided
    symbols_to_scan = cfg.get("SYMBOLS")
    if symbols_to_scan:
        filtered_tickers = [ticker for ticker in tickers if ticker.get("symbol") in symbols_to_scan]
        logger.info(f"Filtered to {len(filtered_tickers)} symbols from request")
    else:
        filtered_tickers = [
            ticker for ticker in tickers
            if float(ticker.get("turnoverRv") or 0.0) >= cfg["MIN_VOLUME"]
        ]

    results = []
    if not filtered_tickers:
        return []

    import concurrent.futures
    import traceback
    num_workers = min(cfg["MAX_WORKERS"], max(1, len(filtered_tickers)))

    # Concurrent execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(module.analyse, t, cfg, not args.no_ai, not args.no_entity, None) for t in filtered_tickers]
        for future in concurrent.futures.as_completed(futures):
            try:
                # REF: [Tier 3] Descriptive Naming
                scan_res = future.result()
                if scan_res:
                    scan_res["scan_timestamp"] = datetime.datetime.now(datetime.timezone.utc)
                    results.append(scan_res)
                    if on_result:
                        # REF: [Tier 1] Critical Thread Error Handling
                        def _result_wrapper(res, dr):
                            try:
                                on_result(res, dr)
                            except Exception as error:
                                logger.error(f"On-result callback crashed: {error}\n{traceback.format_exc()}")
                        threading.Thread(target=_result_wrapper, args=(scan_res, direction), daemon=True).start()
            except Exception as error:
                logger.error(f"Analysis task failed: {error}\n{traceback.format_exc()}")

    return results


def _effective_score(result: dict) -> float:
    signals = result.get("signals", [])
    base    = result.get("score", 0)
    bonus   = 15 if any("HTF Alignment" in s for s in signals) else 0
    penalty = 20 if any("Low Liquidity" in s for s in signals) else 0
    return base + bonus - penalty


def pick_candidates(
    long_results: List[dict],
    short_results: List[dict],
    min_score: int,
    min_score_gap: int,
    direction_filter: str,
    symbols_in_position: set,
    available_slots: int,
    min_signals: int = 3,
) -> List[Tuple[dict, str]]:
    """
    Selects the best trading candidates based on scores and available slots.
    REF: [Tier 2] Logic Improvements
    """
    # REF: [Tier 3] Descriptive Naming
    symbol_scores = {}
    for scan_res in long_results:
        symbol_scores.setdefault(scan_res["inst_id"], {"LONG": 0, "SHORT": 0})["LONG"] = scan_res["score"]
    for scan_res in short_results:
        symbol_scores.setdefault(scan_res["inst_id"], {"LONG": 0, "SHORT": 0})["SHORT"] = scan_res["score"]

    candidates = []

    if direction_filter.upper() in ["LONG", "BOTH"]:
        for scan_res in long_results:
            if scan_res["score"] < min_score:
                continue
            if len(scan_res.get("signals", [])) < min_signals:
                continue
            if scan_res["inst_id"] in symbols_in_position:
                continue
            scores = symbol_scores.get(scan_res["inst_id"], {"LONG": 0, "SHORT": 0})
            if scores["LONG"] - scores["SHORT"] < min_score_gap:
                continue
            candidates.append((scan_res, "LONG"))

    if direction_filter.upper() in ["SHORT", "BOTH"]:
        for scan_res in short_results:
            if scan_res["score"] < min_score:
                continue
            if len(scan_res.get("signals", [])) < min_signals:
                continue
            if scan_res["inst_id"] in symbols_in_position:
                continue
            scores = symbol_scores.get(scan_res["inst_id"], {"LONG": 0, "SHORT": 0})
            if scores["SHORT"] - scores["LONG"] < min_score_gap:
                continue
            candidates.append((scan_res, "SHORT"))

    # Sort by quality-adjusted score, not raw
    candidates.sort(key=lambda item: _effective_score(item[0]), reverse=True)
    return candidates[:available_slots]


# ────────────────────────────────────────────────────────────────────
# Print helpers
# ────────────────────────────────────────────────────────────────────

def print_positions(positions: List[dict]):
    if not positions:
        print(Fore.WHITE + " No open positions.")
        return
    for p in positions:
        pnl = p.get("pnl", 0.0)
        pnl_color = Fore.GREEN if pnl >= 0 else Fore.RED
        side_color = Fore.GREEN if p["side"] == "Buy" else Fore.RED

        # Duration info from _local_stop_states
        dur_str = ""
        if p["symbol"] in _local_stop_states:
            ls = _local_stop_states[p["symbol"]]
            et = ls.get("entry_time")
            if et:
                if et.tzinfo is None:
                    et = et.replace(tzinfo=datetime.timezone.utc)
                diff = datetime.datetime.now(datetime.timezone.utc) - et
                tot_sec = int(diff.total_seconds())
                if tot_sec < 60:
                    dur_str = f"({tot_sec}s)"
                elif tot_sec < 3600:
                    dur_str = f"({tot_sec // 60}m)"
                else:
                    dur_str = f"({tot_sec // 3600}h {(tot_sec % 3600) // 60}m)"

        print(
            f"  {side_color}{'▲' if p['side']=='Buy' else '▼'} {p['symbol']:<16}{Style.RESET_ALL}"
            f" Size: {p['size']}  Entry: {p['entry']:.4g} "
            f" PnL: {pnl_color}{pnl:+.4f} USDT{Style.RESET_ALL} {Fore.WHITE}{dur_str}{Style.RESET_ALL}"
        )


def print_candidates(candidates: List[Tuple[dict, str]]):
    if not candidates:
        print(Fore.YELLOW + " No candidates pass min-score or available slots.")
        return
    # REF: Tier 3: Non-Descriptive Variable Naming (r -> scan_res)
    for scan_res, direction in candidates:
        dir_color = Fore.GREEN if direction == "LONG" else Fore.RED
        from core.phemex_short import grade
        g, gc = grade(scan_res["score"])
        print(
            f"  {dir_color}{'▲' if direction=='LONG' else '▼'} {scan_res['inst_id']:<16}{Style.RESET_ALL}"
            f" Score: {gc}{scan_res['score']}{Style.RESET_ALL} ({g}) "
            f" Price: {scan_res['price']:.4g} "
            f" RSI: {scan_res.get('rsi') or 0:.1f} "
            f" Funding: {(scan_res.get('funding_pct') or 0):+.4f}%"
        )


# ────────────────────────────────────────────────────────────────────
# Main bot loop
# ────────────────────────────────────────────────────────────────────

# ── Cluster & Entropy Tracking (Idea 2 & 3) ─────────────────────────
_hawkes_long = pc.HawkesTracker(mu=0.1, alpha=0.8, beta=0.1)
_hawkes_short = pc.HawkesTracker(mu=0.1, alpha=0.8, beta=0.1)
_entropy_penalty = 0

def _get_cluster_threshold_penalty(intensity: float) -> int:
    """Returns a score penalty based on Hawkes intensity (λ)."""
    if intensity > 3.0:
        return 50  # Major cluster - raise bar significantly
    if intensity > 2.0:
        return 30
    if intensity > 1.2:
        return 15
    return 0

def bot_loop(args):
    global _account_high_water, _account_trail_stop, _account_trading_halted, _cached_balance, _cached_positions, _entropy_penalty, _bot_state
    _bot_state = BotState()

    cfg = {
        "MIN_VOLUME": args.min_vol,
        "TIMEFRAME":  args.timeframe,
        "CANDLES":    500,
        "TOP_N":      50,    # scan wide, filter later
        "MIN_SCORE":  0,     # don't filter in scanner, we'll do it here
        "MAX_WORKERS": args.workers,
        "RATE_LIMIT_RPS": args.rate,
    }

    # Initial account state
    _cached_balance = get_balance() or 0.0
    _cached_positions = get_open_positions()
    _account_high_water = _cached_balance + sum([p.get("pnl", 0.0) for p in _cached_positions])
    _account_trail_stop = _account_high_water * (1 - ACCOUNT_TRAIL_PCT)

    with _equity_lock:
        _session_equity_history.append(_account_high_water)

    load_blacklist() # Load persistent blacklist at startup

    # Load recent trade history for recovery
    history = _read_trade_log()

    # Start WebSocket, Dashboard and Cache Refresher
    _ensure_ws_started()
    for p in _cached_positions:
        _subscribe_symbol(p["symbol"])

    # Populate local stop state for existing positions so they can be tracked
    for p in _cached_positions:
        if p["symbol"] not in _local_stop_states:
            # Find the most recent 'entered' status for this symbol in history
            h_entry = next((h for h in reversed(history) if h.get("symbol") == p["symbol"] and h.get("status") == "entered"), None)
            entry_time = datetime.datetime.now(datetime.timezone.utc)
            entry_score = 0
            if h_entry:
                try:
                    entry_time = datetime.datetime.fromisoformat(h_entry["timestamp"])
                    # REF: Safety check for naive datetimes from history
                    if entry_time.tzinfo is None:
                        entry_time = entry_time.replace(tzinfo=datetime.timezone.utc)
                    entry_score = h_entry.get("score", 0)
                except Exception as e:
                    logger.warning(f"Failed to parse entry timestamp for {p['symbol']} from history: {e}")

            _local_stop_states[p["symbol"]] = {
                "stop_price": 0, # Unknown initially
                "entry_time": entry_time,
                "entry_score": entry_score,
                "direction": "LONG" if p["side"] == "Buy" else "SHORT",
            }

    global _display_thread_running
    if not _display_thread_running:
        _display_thread_running = True
        # REF: [Tier 1] Critical Thread Error Handling
        def _display_wrapper():
            import traceback
            try:
                _live_pnl_display()
            except Exception as error:
                logger.error(f"Display thread crashed: {error}\n{traceback.format_exc()}")

        def _cache_wrapper():
            import traceback
            try:
                _cache_refresher()
            except Exception as error:
                logger.error(f"Cache refresher thread crashed: {error}\n{traceback.format_exc()}")

        if not getattr(args, 'no_tui', False):
            threading.Thread(target=_display_wrapper, daemon=True).start()
        threading.Thread(target=_cache_wrapper, daemon=True).start()

    # ── Drawdown guard initialisation ────────────────────────────────
    if _DD_OK:
        drawdown_guard.set_start_balance(_cached_balance)
        logger.info(f"[DD] Drawdown guard armed — start balance: {_cached_balance:.2f} USDT")

    # ── Telegram controller startup ───────────────────────────────────
    if _TG_OK:
        def _get_live_balance():
            return _cached_balance
        def _get_live_positions():
            return _cached_positions
        def _get_live_stats():
            with _cache_lock:
                upnl = sum(p.get("pnl", 0.0) for p in _cached_positions)
            return {
                "wins": _session_wins,
                "losses": _session_losses,
                "total_pnl": _session_total_pnl + upnl
            }

        telegram.start(
            get_balance_fn     = _get_live_balance,
            get_positions_fn   = _get_live_positions,
            get_session_pnl_fn = _get_live_stats,
            get_logs_fn        = _get_tui_logs,
            run_scan_fn        = _manual_tg_scan,
            get_chart_fn       = _get_session_chart,
            run_backtest_fn    = _run_manual_backtest
        )
        logger.info("[TG] Telegram controller started")

    # ── Web Bridge initialization ──
    with _bot_state.lock:
        _bot_state.balance = _cached_balance
        _bot_state.positions = _cached_positions
        _bot_state.max_positions = get_dynamic_max_positions(_cached_balance)
    
    if getattr(args, "web", False):
        web_bridge.start_bridge_thread(_bot_state, _bot_logs, port=args.web_port)
        logger.info(f"[WEB] Web dashboard bridge started on port {args.web_port}")

    scan_number = 0
    # ── Main Bot Execution Loop ──────────────────────────────────────
    while _running:
        try:
            if not getattr(args, 'no_tui', False):
                _process_animations()
            # ── Time-of-day Profitability Filter ──
            if pc.is_hour_blocked():
                curr_hour = datetime.datetime.now(datetime.timezone.utc).hour
                tui_log(f"HOUR FILTER: Skipping scan cycle — hour {curr_hour} UTC is blocked.", event_type="SKIP")
                time.sleep(60)
                continue

            # ── Event/News Suppression Filter ──
            if _EF_OK:
                suppressed, reason = event_filter.filter.should_suppress()
                if suppressed:
                    tui_log(f"EVENT FILTER: Skipping scan cycle — {reason}", event_type="SKIP")
                    time.sleep(60)
                    continue

            if _account_trading_halted:
                time.sleep(30)
                continue

            # ── Drawdown kill-switch check ────────────────────────────────
            if _DD_OK:
                _dd_ok_flag, _dd_reason = drawdown_guard.can_open_trade(_cached_balance)
                if not _dd_ok_flag:
                    tui_log(f"[DD] Trading halted by drawdown guard: {_dd_reason}", event_type="HALT")
                    time.sleep(60)
                    continue

            # ── Telegram halt check ───────────────────────────────────────
            if _TG_OK and telegram.is_halted():
                tui_log("[TG] Trading halted via Telegram /stop command", event_type="HALT")
                time.sleep(30)
                continue

            scan_number += 1

            # ── Account status ───────────────────────────────────────────
            with _cache_lock:
                # Dynamic scaling: more positions as equity grows
                dynamic_max = get_dynamic_max_positions(_cached_balance)
                available_slots = dynamic_max - len(_cached_positions)
            
            # Update _bot_state for web dashboard
            with _bot_state.lock:
                _bot_state.balance = _cached_balance
                _bot_state.positions = _cached_positions
                _bot_state.max_positions = dynamic_max
                with _prices_lock:
                    _bot_state.live_prices = _live_prices.copy()
                _bot_state.entropy_penalty = _entropy_penalty
                _bot_state.is_running = True
                _bot_state.rolling_stats = {
                    "wins": _session_wins,
                    "losses": _session_losses,
                    "win_pnl": _session_total_pnl if _session_total_pnl > 0 else 0, # Best guess
                    "loss_pnl": _session_total_pnl if _session_total_pnl < 0 else 0
                }

            # ── Fast-track callback ──────────────────────────────────────
            # REF: Tier 3: Non-Descriptive Variable Naming (r -> scan_res)
            def on_scan_result(scan_res, direction, args=args):
                if not _running or _account_trading_halted:
                    return
                if _DD_OK:
                    _ok, _reason = drawdown_guard.can_open_trade(_cached_balance)
                    if not _ok:
                        return
                if _TG_OK and telegram.is_halted():
                    return

                # ── Hawkes Cluster Throttling (Idea 3) ────────────────────
                tracker = _hawkes_long if direction == "LONG" else _hawkes_short
                intensity = tracker.get_intensity() # Check intensity WITHOUT updating it yet
                hawkes_penalty = _get_cluster_threshold_penalty(intensity)

                # Use global _entropy_penalty from last scan to block cascades
                effective_fast_track = FAST_TRACK_SCORE + hawkes_penalty + _entropy_penalty
                if scan_res["score"] < effective_fast_track:
                    if hawkes_penalty > 0 or _entropy_penalty > 0:
                        tui_log(f"FT THROTTLE: {scan_res['inst_id']} score {scan_res['score']} < dynamic FT threshold {effective_fast_track} (λ={intensity:.2f}, H_pen={_entropy_penalty})", event_type="THROTTLE")
                    return

                # Signal passed! Now update the tracker to throttle the NEXT one in this cluster.
                intensity = tracker.update(event_occurred=True)

                # Staleness check
                result_time = scan_res.get("scan_timestamp")
                if result_time:
                    if result_time.tzinfo is None:
                        result_time = result_time.replace(tzinfo=datetime.timezone.utc)
                    if (datetime.datetime.now(datetime.timezone.utc) - result_time).total_seconds() > RESULT_STALENESS_SECONDS:
                        return

                with _fast_track_lock:
                    with _cache_lock:
                        # Account for positions already open PLUS those currently being verified
                        if len(_cached_positions) + len(_fast_track_opened) >= get_dynamic_max_positions(_cached_balance):
                            return
                        if scan_res["inst_id"] in {p["symbol"] for p in _cached_positions}:
                            return

                    if scan_res["inst_id"] in _fast_track_opened:
                        return

                    # Cooldown check
                    last_ft = FAST_TRACK_COOLDOWN.get(scan_res["inst_id"], 0)
                    if time.time() - last_ft < FAST_TRACK_COOLDOWN_SECONDS:
                        return

                    # ── AI Thesis Generation (Machine Intelligence Upgrade) ──
                    if scan_res["score"] > 130:
                        def _fetch_thesis(res, dr):
                            prompt = f"Analyze {dr} signal for {res['inst_id']} (Score: {res['score']}). Signals: {', '.join(res['signals'])}. Sector: {res.get('sector', 'Unknown')}. Liquidity Spectre: {res.get('spectre_score', 0)}. Provide a 1-sentence aggressive trading thesis."

                            def _token_cb(token):
                                if not _thesis_log or res['inst_id'] not in _thesis_log[-1]:
                                    _thesis_log.append(f"[{res['inst_id']}] ")
                                _thesis_log[-1] += token

                            pc.call_deepseek(prompt, output_callback=_token_cb)

                        threading.Thread(target=_fetch_thesis, args=(scan_res, direction), daemon=True).start()

                    _fast_track_opened.add(scan_res["inst_id"])
                    FAST_TRACK_COOLDOWN[scan_res["inst_id"]] = time.time()

                try:
                    tui_log(f"FAST-TRACK: {scan_res['inst_id']} scored {scan_res['score']} — opening immediately (λ={intensity:.2f})", event_type="FAST")

                    # Entity API Hook: Fast Track signal
                    make_entity_request("signalevent", data={
                        "signal_id": f"sig-{scan_res['inst_id']}-{int(time.time())}",
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "symbol": scan_res["inst_id"],
                        "direction": direction,
                        "raw_score": scan_res["score"],
                        "effective_score": _effective_score(scan_res),
                        "passed_quality_gate": True,
                        "executed": True,
                        "skip_reason": "FAST_TRACK",
                        "hawkes_intensity": intensity,
                        "entropy_penalty": _entropy_penalty
                    })

                    # ── Wait & Verify ────────────────────────────────────
                    # Release lock during verify_candidate (15s sleep) and setup
                    verified_result = verify_candidate(scan_res["inst_id"], direction, scan_res["score"])

                    if verified_result:
                        ok = execute_setup(verified_result, direction, dry_run=args.dry_run)
                        if ok:
                            _subscribe_symbol(scan_res["inst_id"])
                            time.sleep(2)
                finally:
                    with _fast_track_lock:
                        if scan_res["inst_id"] in _fast_track_opened:
                            _fast_track_opened.remove(scan_res["inst_id"])

            # ── Scan ─────────────────────────────────────────────────────
            if _cached_balance < LOW_LIQ_MARGIN: # allow entry even in low-liq mode ($5 min)
                 # Wait if balance is critical
                 time.sleep(args.interval)
                 continue

            if available_slots <= 0:
                time.sleep(args.interval)
                continue

            _display_paused.set() # pause dashboard during scan output to avoid mess
            # REF: Tier 3: Non-Descriptive Variable Naming (long_r, short_r -> long_results, short_results)
            long_results, short_results = run_scanner_both(cfg, args, on_result=on_scan_result)
            _display_paused.clear()

            # ── Cross-Asset Entropy Deflator (Idea 2) ─────────────────────
            # Fetch total tickers again to get the universe size for entropy calculation
            all_tickers = pc.get_tickers(rps=args.rate)
            total_universe = len([t for t in all_tickers if float(t.get("turnoverRv", 0)) >= args.min_vol])

            n_hits = len(long_results) + len(short_results)
            imbalance = 0.0
            if total_universe > 0 and n_hits > 0:
                # Saturation: percentage of universe firing
                sat_ratio = n_hits / total_universe
                # Capped and less aggressive entropy penalties
                sat_penalty = min(ENTROPY_SAT_CAP, int(sat_ratio * ENTROPY_SAT_WEIGHT)) 

                # One-sidedness: how imbalanced are the signals?
                imbalance = abs(len(long_results) - len(short_results)) / n_hits
                side_penalty = int(ENTROPY_IMB_WEIGHT * imbalance)

                _entropy_penalty = min(ENTROPY_MAX_PENALTY, sat_penalty + side_penalty)
            else:
                _entropy_penalty = 0

            if _entropy_penalty > ENTROPY_ALERT_LEVEL:
                tui_log(f"ENTROPY DEFLATOR: Raising min_score by +{_entropy_penalty} (Saturation: {n_hits}/{total_universe}, Imbalance: {imbalance:.2f})", event_type="DEFLATOR")

            # ── Dynamic threshold calculation ─────────────────────────────
            eff_min_score = args.min_score + _entropy_penalty
            if not args.no_dynamic:
                # REF: Tier 3: Non-Descriptive Variable Naming (r -> res)
                all_scores = [res["score"] for res in (long_results + short_results)]
                dynamic_min = pc.calc_dynamic_threshold(all_scores, args.min_score)
                eff_min_score = max(eff_min_score, dynamic_min)

            if eff_min_score > args.min_score:
                tui_log(f"ADAPTIVE FILTER: Effective Min Score: {eff_min_score} (Penalty: +{_entropy_penalty})", event_type="ADAPTIVE")

            # Update _bot_state for web dashboard
            with _bot_state.lock:
                _bot_state.scan_count = scan_number
                # Combine results for dashboard display
                tagged_long = [dict(r, dir="LONG", status="PASS" if r["score"] >= eff_min_score else "low score") for r in long_results]
                tagged_short = [dict(r, dir="SHORT", status="PASS" if r["score"] >= eff_min_score else "low score") for r in short_results]
                # Map inst_id to symbol for dashboard
                for r in tagged_long + tagged_short:
                    r["symbol"] = r.get("inst_id")
                _bot_state.last_scanner_results = sorted(tagged_long + tagged_short, key=lambda x: x["score"], reverse=True)
                _bot_state.analyzed_count += len(long_results) + len(short_results)

            # ── Staleness filter ──────────────────────────────────────────
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            def is_result_fresh(res, now):
                ts = res.get("scan_timestamp")
                if not ts:
                    return True
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.timezone.utc)
                return (now - ts).total_seconds() < RESULT_STALENESS_SECONDS

            fresh_long  = [res for res in long_results  if is_result_fresh(res, now_utc)]
            fresh_short = [res for res in short_results if is_result_fresh(res, now_utc)]

            # ── Pick candidates ──────────────────────────────────────
            with _cache_lock:
                in_pos_updated    = {p["symbol"] for p in _cached_positions}
                available_updated = get_dynamic_max_positions(_cached_balance) - len(_cached_positions)

            candidates = pick_candidates(
                fresh_long, fresh_short,
                min_score=eff_min_score,
                min_score_gap=args.min_score_gap,
                direction_filter=args.direction,
                symbols_in_position=in_pos_updated,
                available_slots=available_updated,
                min_signals=args.min_signals,
            )

            # ── Execute ──────────────────────────────────────────────
            sleep_interval = args.interval
            if candidates:
                for result, direction in candidates:
                    with _cache_lock:
                        # Recheck available slots before executing each candidate,
                        # as a fast-track or manual action might have filled a slot
                        if len(_cached_positions) >= get_dynamic_max_positions(_cached_balance):
                            # REF: Tier 3: Temporal Inconsistency
                            make_entity_request("signalevent", data={
                                "signal_id": f"sig-{result['inst_id']}-{int(time.time())}",
                                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                "symbol": result["inst_id"],
                                "direction": direction,
                                "raw_score": result["score"],
                                "effective_score": _effective_score(result),
                                "passed_quality_gate": True,
                                "executed": False,
                                "skip_reason": "MAX_POSITIONS_AFTER_SCAN_CANDIDATE"
                            })
                            tui_log(f"SKIP: {result['inst_id']} - Max positions reached while processing candidates.", event_type="SKIP")
                            continue # Skip this candidate

                    # Entity API Hook: Execute Candidate
                    # REF: Tier 3: Temporal Inconsistency
                    make_entity_request("signalevent", data={
                        "signal_id": f"sig-{result['inst_id']}-{int(time.time())}",
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "symbol": result["inst_id"],
                        "direction": direction,
                        "raw_score": result["score"],
                        "effective_score": _effective_score(result),
                        "passed_quality_gate": True,
                        "executed": True,
                        "skip_reason": "SCAN_CANDIDATE"
                    })

                    # ── Wait & Verify ────────────────────────────────────
                    # scan -> select -> wait -> verify -> enter
                    verified_result = verify_candidate(result["inst_id"], direction, result["score"])
                    if not verified_result:
                        continue # Verification failed, skip to next candidate

                    ok = execute_setup(verified_result, direction, dry_run=args.dry_run)
                    if ok:
                        _subscribe_symbol(result["inst_id"])
                        time.sleep(2)
            else:
                # If no candidates, scan more frequently
                logger.info("No qualifying candidates found. Shortening scan interval.")
                sleep_interval = 5 # Check every 5 seconds if nothing is found

            # ── Sleep ────────────────────────────────────────────────────
            _slot_available_event.wait(timeout=sleep_interval)
            _slot_available_event.clear()

        except Exception as e:
            logger.error(f"Main loop error: {e}. Backing off for 30s...")
            import traceback
            logger.debug(traceback.format_exc())
            time.sleep(30)

    tui_log("Bot shutdown requested. Closing components...", event_type="HALT")
    if _ws_app:
        _ws_app.close()


# ────────────────────────────────────────────────────────────────────
# One-shot mode: single scan + execute
# ────────────────────────────────────────────────────────────────────

def one_shot(args):
    """Run a single scan, print results, and optionally execute top setup."""
    cfg = {
        "MIN_VOLUME": args.min_vol,
        "TIMEFRAME":  args.timeframe,
        "CANDLES":    500,
        "TOP_N":      50,
        "MIN_SCORE":  0,
        "MAX_WORKERS": args.workers,
        "RATE_LIMIT_RPS": args.rate,
    }

    load_blacklist() # Load persistent blacklist at startup

    balance   = get_balance()
    positions = get_open_positions()
    in_pos    = {p["symbol"] for p in positions}

    print(Fore.CYAN + Style.BRIGHT + f"\n{'='*70}")
    print(Fore.CYAN + Style.BRIGHT + " 🔍 ONE-SHOT SCAN")
    print(Fore.CYAN + Style.BRIGHT + f"{'='*70}")
    print(f" Balance: {f'{balance:.2f}' if balance is not None else '?'} USDT | "
          f"Positions: {len(positions)}/{MAX_POSITIONS}\n")

    print_positions(positions)

    print(Fore.WHITE + f"\n Running scanners ({args.timeframe})...")
    long_r, short_r = run_scanner_both(cfg, args)

    # ── Dynamic threshold ─────────────────────────────────────────────
    eff_min_score = args.min_score
    if not args.no_dynamic:
        # REF: Tier 3: Non-Descriptive Variable Naming (r -> res)
        all_scores = [res["score"] for res in (long_r + short_r)]
        eff_min_score = pc.calc_dynamic_threshold(all_scores, args.min_score)
        if eff_min_score > args.min_score:
            print(Fore.YELLOW + f" [ADAPTIVE FILTER] Dynamic Min Score: {eff_min_score}")

    available_slots = MAX_POSITIONS - len(positions)
    candidates = pick_candidates(
        long_r, short_r,
        min_score=eff_min_score,
        min_score_gap=args.min_score_gap,
        direction_filter=args.direction,
        symbols_in_position=in_pos,
        available_slots=available_slots,
        min_signals=args.min_signals
    )

    print(f"\n Scan complete — Longs: {len(long_r)}  Shorts: {len(short_r)}")
    print(f" Candidates (score ≥ {eff_min_score}): {len(candidates)}\n")

    print_candidates(candidates)

    if candidates and not args.dry_run:
        print()
        confirm = input(Fore.YELLOW + " Execute top candidate? [y/N]: ").strip().lower()
        if confirm == "y":
            top_result, top_dir = candidates[0]
            # ── Wait & Verify ────────────────────────────────────
            verified_result = verify_candidate(top_result["inst_id"], top_dir, top_result["score"])
            if verified_result:
                execute_setup(verified_result, top_dir, dry_run=False)
            else:
                print(Fore.RED + " [FAIL] Verification failed — entry cancelled.")
        else:
            print(Fore.YELLOW + " Skipped.")
    elif candidates and args.dry_run:
        print()
        # REF: Tier 3: Non-Descriptive Variable Naming (r, d -> scan_res, direction)
        for scan_res, direction in candidates:
            # ── Wait & Verify (DRY RUN) ──────────────────────────
            verified_result = verify_candidate(scan_res["inst_id"], direction, scan_res["score"])
            if verified_result:
                execute_setup(verified_result, direction, dry_run=True)
            else:
                print(Fore.RED + f" [FAIL] {scan_res['inst_id']} failed verification.")


# ────────────────────────────────────────────────────────────────────
# Status command
# ────────────────────────────────────────────────────────────────────

def show_status():
    print(Fore.CYAN + Style.BRIGHT + f"\n {'='*60}")
    print(Fore.CYAN + Style.BRIGHT + "  BOT STATUS")
    print(Fore.CYAN + Style.BRIGHT + f" {'='*60}")
    print(f" Exchange  : {BASE_URL}")
    print(f" API Key   : {API_KEY[:8]}..." if API_KEY else " API Key   : NOT SET ⚠")
    print(f" Margin    : ${MARGIN_USDT} @ {LEVERAGE}x = ${MARGIN_USDT*LEVERAGE:.0f} notional")
    print(f" Trail     : {TRAIL_PCT*100:.1f}%")
    print(f" Max Pos   : {MAX_POSITIONS}")
    print(f" Min Score : {MIN_SCORE}")
    print()

    balance, positions = get_account_status()
    balance_display = f"{balance:.4f}" if balance is not None else "ERROR"
    print(f" Balance   : {balance_display} USDT")

    print(f" Positions ({len(positions)}/{MAX_POSITIONS}):")
    print_positions(positions)

    # Recent trades
    try:
        trades = _read_trade_log()
        if trades:
            print(f"\n Recent trades ({len(trades)} total):")
            for t in trades[-5:][::-1]:
                dr = "DRY" if t.get("dry_run") else "LIVE"
                print(f"  {t['timestamp'][:19]} {t.get('direction','?'):5} "
                      f"{t['symbol']:<16} Score:{t['score']} "
                      f"@{t['price']:.4g} [{dr}]")
    except Exception:
        pass
    print()


# ────────────────────────────────────────────────────────────────────
# One-shot deploy mode: single scan + exit
# ────────────────────────────────────────────────────────────────────

def deploy_once(args):
    """Run a single scan, execute top candidates, and exit."""
    cfg = {
        "MIN_VOLUME": args.min_vol,
        "TIMEFRAME":  args.timeframe,
        "CANDLES":    500,
        "TOP_N":      50,
        "MIN_SCORE":  0,
        "MAX_WORKERS": args.workers,
        "RATE_LIMIT_RPS": args.rate,
    }

    load_blacklist() # Load persistent blacklist at startup

    # Refresh current status
    balance   = get_balance()
    positions = get_open_positions()
    in_pos    = {p["symbol"] for p in positions}
    available_slots = MAX_POSITIONS - len(positions)

    print(Fore.CYAN + Style.BRIGHT + f"\n{'='*70}")
    print(Fore.CYAN + Style.BRIGHT + " 🚀 ONE-SHOT DEPLOY")
    print(Fore.CYAN + Style.BRIGHT + f"{'='*70}")
    print(f" Balance: {balance:.2f} USDT | Available slots: {available_slots}/{MAX_POSITIONS}\n")

    if available_slots <= 0:
        print(Fore.YELLOW + " All position slots filled — exiting.")
        return

    print(Fore.WHITE + f" Running scanners ({args.timeframe})...")
    long_r, short_r = run_scanner_both(cfg, args)

    # ── Dynamic threshold ─────────────────────────────────────────────
    eff_min_score = args.min_score
    if not args.no_dynamic:
        # REF: Tier 3: Non-Descriptive Variable Naming (r -> res)
        all_scores = [res["score"] for res in (long_r + short_r)]
        eff_min_score = pc.calc_dynamic_threshold(all_scores, args.min_score)
        if eff_min_score > args.min_score:
            print(Fore.YELLOW + f" [ADAPTIVE FILTER] Dynamic Min Score: {eff_min_score}")

    candidates = pick_candidates(
        long_r, short_r,
        min_score=eff_min_score,
        min_score_gap=args.min_score_gap,
        direction_filter=args.direction,
        symbols_in_position=in_pos,
        available_slots=available_slots,
        min_signals=args.min_signals,
    )

    print(f"\n Scan complete — Longs: {len(long_r)}  Shorts: {len(short_r)}")
    print(f" Qualifying Candidates (score ≥ {eff_min_score}): {len(candidates)}\n")

    print_candidates(candidates)

    if not candidates:
        print(Fore.YELLOW + " No candidates found — exiting.")
        return

    opened_count = 0
    deployed_summary = []

    print()
    for result, direction in candidates:
        if opened_count >= available_slots:
            break

        # ── Wait & Verify ────────────────────────────────────
        verified_result = verify_candidate(result["inst_id"], direction, result["score"])
        if not verified_result:
            continue

        ok = execute_setup(verified_result, direction, dry_run=args.dry_run)
        if ok:
            opened_count += 1
            # Retrieve last stop from _local_stop_states for summary
            stop_info = _local_stop_states.get(result["inst_id"], {})
            stop_price = stop_info.get("stop_price", 0)

            deployed_summary.append({
                "symbol": result["inst_id"],
                "dir": direction,
                "price": result["price"],
                "stop": stop_price,
                "score": result["score"]
            })
            time.sleep(2)

    if deployed_summary:
        msg_header = "🔥 *DEPLOYMENT COMPLETE*\n\n"
        msg_lines = []
        for s in deployed_summary:
            emoji = "▲" if s["dir"] == "LONG" else "▼"
            msg_lines.append(f"{emoji} *{s['symbol']}* @ {s['price']:.4g} (Stop: {s['stop']:.4g}) | Score: {s['score']}")
        msg = msg_header + "\n".join(msg_lines)
        send_telegram_message(msg)
        print(Fore.GREEN + Style.BRIGHT + "\n ✅ Deployment summary sent to Telegram.")

    print(Fore.CYAN + "\n Deployment task finished. Exiting.\n")


def verify_trailing_stops():
    """
    Verifies if trailing stop orders are present by querying the Phemex API.
    This function is intended for post-deployment verification.
    """
    # Read credentials locally — never mutate module-level API_KEY / API_SECRET
    local_key    = os.getenv("PHEMEX_API_KEY", API_KEY)
    local_url    = os.getenv("PHEMEX_BASE_URL", BASE_URL)

    print("--- Verifying Trailing Stop Orders ---")
    print(f"Using API Key: {local_key[:8]}...")
    print(f"Base URL: {local_url}")

    # REF: Tier 3: Non-Descriptive Variable Naming (r -> resp)
    resp = _get('/g-orders/activeList', {'currency': 'USDT', 'ordStatus': 'Untriggered'})
    if resp and resp.get('code') == 0:
        print("Verification successful. Active Untriggered Orders:")
        print(json.dumps(resp.get('data', {}), indent=2))

        trailing_stops = [
            order for order in resp.get('data', {}).get('rows', [])
            if order.get('ordType') == 'Stop' and order.get('pegPriceType') == 'TrailingStopPeg'
        ]

        if trailing_stops:
            print("\n--- Found TrailingStopPeg Orders ---")
            print(json.dumps(trailing_stops, indent=2))
        else:
            print("\n--- No TrailingStopPeg Orders Found ---")
    else:
        print("Verification failed or no active untriggered orders found.")
        if resp:
            print(f"API Response Code: {resp.get('code')}, Message: {resp.get('msg')}")
        else:
            print("No response from API.")
    print("------------------------------------")


# ────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────

def main():
    if not API_KEY or not API_SECRET:
        print(Fore.RED + "[ERROR] PHEMEX_API_KEY and PHEMEX_API_SECRET must be set in .env")
        print(" Example .env:")
        print("  PHEMEX_API_KEY=your-key-id")
        print("  PHEMEX_API_SECRET=your-secret")
        print("  PHEMEX_BASE_URL=https://testnet-api.phemex.com")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Phemex Automated Trading Bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # ── run: continuous loop ─────────────────────────────────────────
    run_p = sub.add_parser("run", help="Run the bot in a continuous scan loop")
    run_p.add_argument("--interval",  type=int,   default=SCAN_INTERVAL, help="Seconds between scans")
    run_p.add_argument("--min-score", type=int,   default=MIN_SCORE, help="Min score to execute")
    run_p.add_argument("--min-score-gap", type=int, default=MIN_SCORE_GAP, help="Min score gap (Long - Short) for entry")
    run_p.add_argument("--min-signals", type=int, default=3, help="Minimum number of signals required to consider a candidate (default: 3)")
    run_p.add_argument("--direction", default=DIRECTION, choices=["LONG", "SHORT", "BOTH"], help="Direction to trade")
    run_p.add_argument("--timeframe", default=TIMEFRAME, help="Scanner timeframe")
    run_p.add_argument("--cooldown", type=int, default=4, help="Cooldown in candles after exit")
    run_p.add_argument("--min-vol",   type=int,   default=MIN_VOLUME, help="Min 24h USDT volume")
    run_p.add_argument("--workers",   type=int,   default=MAX_WORKERS, help="Scanner threads")
    run_p.add_argument("--rate",      type=float, default=RATE_LIMIT_RPS, help="API requests/sec")
    run_p.add_argument("--dry-run",   action="store_true", help="Don't place real orders")
    run_p.add_argument("--yes",       action="store_true", help="Skip mainnet confirmation")
    run_p.add_argument("--no-ai",     action="store_true", help="Disable AI commentary")
    run_p.add_argument("--no-entity", action="store_true", help="Disable Entity API")
    run_p.add_argument("--no-dynamic", action="store_true", help="Disable adaptive score filtering")
    run_p.add_argument("--no-tui",     action="store_true", help="Run without the full-screen TUI dashboard")
    run_p.add_argument("--web",        action="store_true", help="Enable web dashboard backend")
    run_p.add_argument("--web-port",   type=int, default=8081, help="Port for web dashboard backend")

    # ── deploy: single scan + exit ───────────────────────────────────
    deploy_p = sub.add_parser("deploy", help="Run one scan and optionally execute, then exit")
    deploy_p.add_argument("--min-score", type=int,   default=MIN_SCORE)
    deploy_p.add_argument("--min-score-gap", type=int, default=MIN_SCORE_GAP)
    deploy_p.add_argument("--min-signals", type=int, default=3, help="Minimum number of signals required to consider a candidate (default: 3)")
    deploy_p.add_argument("--direction", default=DIRECTION, choices=["LONG", "SHORT", "BOTH"])
    deploy_p.add_argument("--timeframe", default=TIMEFRAME)
    deploy_p.add_argument("--cooldown", type=int, default=4)
    deploy_p.add_argument("--min-vol",   type=int,   default=MIN_VOLUME)
    deploy_p.add_argument("--workers",   type=int,   default=MAX_WORKERS)
    deploy_p.add_argument("--rate",      type=float, default=RATE_LIMIT_RPS)
    deploy_p.add_argument("--dry-run",   action="store_true", help="Print orders but don't execute")
    deploy_p.add_argument("--no-ai",     action="store_true")
    deploy_p.add_argument("--no-entity", action="store_true")
    deploy_p.add_argument("--no-dynamic", action="store_true")

    # ── once: single scan ───────────────────────────────────────────
    once_p = sub.add_parser("once", help="Run one scan and optionally execute")
    once_p.add_argument("--min-score", type=int,   default=MIN_SCORE)
    once_p.add_argument("--min-score-gap", type=int, default=MIN_SCORE_GAP)
    once_p.add_argument("--min-signals", type=int, default=3, help="Minimum number of signals required to consider a candidate (default: 3)")
    once_p.add_argument("--direction", default=DIRECTION, choices=["LONG", "SHORT", "BOTH"])
    once_p.add_argument("--timeframe", default=TIMEFRAME)
    once_p.add_argument("--cooldown", type=int, default=4)
    once_p.add_argument("--min-vol",   type=int,   default=MIN_VOLUME)
    once_p.add_argument("--workers",   type=int,   default=MAX_WORKERS)
    once_p.add_argument("--rate",      type=float, default=RATE_LIMIT_RPS)
    once_p.add_argument("--dry-run",   action="store_true", help="Print orders but don't execute")
    once_p.add_argument("--no-ai",     action="store_true")
    once_p.add_argument("--no-entity", action="store_true")
    once_p.add_argument("--no-dynamic", action="store_true")

    # ── status ──────────────────────────────────────────────────────
    sub.add_parser("status", help="Show account balance, open positions, and recent trades")

    # ── verify-stops ────────────────────────────────────────────────
    sub.add_parser("verify-stops", help="Verify trailing stop orders on the exchange")

    args = parser.parse_args()

    # Update global blacklist duration based on timeframe and cooldown if in run/deploy/once
    if args.command in ["run", "deploy", "once"]:
        # REF: Tier 1: Critical Import Failure Escalation
        if not _SCANNERS_OK:
            print(Fore.RED + f"[ERROR] Could not import scanner modules: {_SCANNER_ERR}")
            print("Scanner modules (phemex_long.py/phemex_short.py) are required for this command.")
            sys.exit(1)

        global BLACKLIST_DURATION_SECONDS
        tf_sec = get_tf_seconds(args.timeframe)
        BLACKLIST_DURATION_SECONDS = args.cooldown * tf_sec
        logger.info(f"Cooldown set to {BLACKLIST_DURATION_SECONDS}s ({args.cooldown} candles)")

    # Entity API: Start Session
    # REF: Tier 3: Temporal Inconsistency
    make_entity_request("botsession", data={
        "session_id": SESSION_ID,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "config": {
            "margin": MARGIN_USDT,
            "leverage": LEVERAGE,
            "trail_pct": TRAIL_PCT,
            "min_score": args.min_score if hasattr(args, "min_score") else MIN_SCORE,
            "min_score_gap": args.min_score_gap if hasattr(args, "min_score_gap") else MIN_SCORE_GAP,
            "direction": args.direction if hasattr(args, "direction") else DIRECTION,
            "timeframe": args.timeframe if hasattr(args, "timeframe") else TIMEFRAME
        },
        "status": "STARTED"
    })

    if args.command == "status":
        show_status()
    elif args.command == "verify-stops":
        verify_trailing_stops()
    elif args.command == "once":
        one_shot(args)
    elif args.command == "deploy":
        testnet = "testnet" in BASE_URL
        env_label = Fore.YELLOW + "⚠ TESTNET" if testnet else Fore.RED + "🚨 MAINNET — REAL MONEY"
        print(Fore.CYAN + Style.BRIGHT + "\n 🚀 Phemex ONE-SHOT DEPLOY Starting")
        print(f" Exchange  : {env_label}{Style.RESET_ALL} ({BASE_URL})")
        print(f" Margin    : ${MARGIN_USDT} @ {LEVERAGE}x = ${MARGIN_USDT*LEVERAGE:.0f} notional")
        print(f" Trail     : {TRAIL_PCT*100:.1f}% | Max Positions: {MAX_POSITIONS}")
        print(f" Min Score : {args.min_score} | Min Gap: {args.min_score_gap} | Direction: {args.direction}")
        if args.dry_run:
            print(Fore.YELLOW + " MODE      : DRY RUN — no real orders will be placed")

        try:
            deploy_once(args)
            make_entity_request("botsession", method="PUT", entity_id=SESSION_ID, data={
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "ended_reason": "DEPLOY_FINISHED",
                "status": "FINISHED"
            })
        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n Deployment stopped by user.")
            make_entity_request("botsession", method="PUT", entity_id=SESSION_ID, data={
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "ended_reason": "USER_INTERRUPT",
                "status": "INTERRUPTED"
            })

    elif args.command == "run":
        testnet = "testnet" in BASE_URL
        env_label = Fore.YELLOW + "⚠ TESTNET" if testnet else Fore.RED + "🚨 MAINNET — REAL MONEY"

        if not args.no_ai:
            play_animation(animations.boot)

        print(Fore.CYAN + Style.BRIGHT + "\n 🤖 Phemex Trading Bot Starting")
        print(f" Exchange  : {env_label}{Style.RESET_ALL} ({BASE_URL})")
        print(f" Margin    : ${MARGIN_USDT} @ {LEVERAGE}x = ${MARGIN_USDT*LEVERAGE:.0f} notional")
        print(f" Trail     : {TRAIL_PCT*100:.1f}% | Max Positions: {MAX_POSITIONS}")
        print(f" Interval  : {args.interval}s | Min Score: {args.min_score}")
        print(f" Min Gap   : {args.min_score_gap} | Direction: {args.direction}")
        if args.dry_run:
            print(Fore.YELLOW + " MODE      : DRY RUN — no real orders will be placed")

        print()

        try:
            bot_loop(args)
        except KeyboardInterrupt:
            print(Fore.YELLOW + "\n\n Bot stopped by user. Shutting down...")
            if _ws_app:
                try:
                    _ws_app.close()
                except Exception:
                    pass
            make_entity_request("botsession", method="PUT", entity_id=SESSION_ID, data={
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "ended_reason": "USER_INTERRUPT",
                "status": "INTERRUPTED"
            })
            print(Fore.YELLOW + " Shutdown complete.")
    else:
        parser.print_help()
        print(f"\n Configured exchange: {BASE_URL}")
        print(f" API key present    : {'YES' if API_KEY else 'NO'}\n")


if __name__ == "__main__":
    main()
