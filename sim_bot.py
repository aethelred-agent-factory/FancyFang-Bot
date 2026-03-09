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
Phemex Simulation (Paper Trading) Bot
======================================
Runs on LIVE production market data but simulates all trades locally.
Maintains a local 'paper_account.json' to track balance and positions.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    import select
    import termios
    import tty

import blessed
import requests
import websocket
from colorama import Fore, Style, init
from dotenv import load_dotenv

import phemex_common as pc
import phemex_long as scanner_long
import phemex_short as scanner_short
import animations

# Safely import p_bot
try:
    import p_bot
except ImportError:
    msg = "CRITICAL: 'p_bot.py' not found. This module is required for risk parameters."
    print(Fore.RED + msg)
    raise pc.InitializationError(msg)

# ── Upgrade modules (graceful degradation if missing) ─────────────────────────
try:
    import signal_analytics as analytics
    _ANALYTICS_OK = True
except ImportError:
    _ANALYTICS_OK = False

try:
    import risk_manager as risk_mgr
    _RISK_MGR_OK = True
except ImportError:
    _RISK_MGR_OK = False

try:
    import drawdown_guard as drawdown_guard
    _DD_OK = True
except ImportError:
    _DD_OK = False

try:
    import telegram_controller as telegram
    _TG_OK = True
except ImportError:
    _TG_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Constants
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR          = Path(__file__).parent

# Initialize colorama for TUI colors
init(autoreset=True)

PAPER_ACCOUNT_FILE  = SCRIPT_DIR / "paper_account.json"
SIM_COOLDOWN_FILE   = SCRIPT_DIR / "sim_cooldowns.json"
INITIAL_BALANCE     = float(os.getenv("INITIAL_BALANCE", "100.0"))
TAKER_FEE_RATE      = pc.TAKER_FEE  # Use common constant (0.06%)

def get_sim_free_margin(balance: float, positions: List[Dict[str, Any]]) -> float:
    """Returns balance not committed to open positions."""
    used = sum(p.get("margin", 0.0) for p in positions)
    return balance - used

MIN_FREE_MARGIN = float(os.getenv("BOT_MIN_FREE_MARGIN", "5.0"))

def pick_sim_leverage(atr_stop_pct: float | None, vol_spike: float = 1.0, is_low_liq: bool = False) -> int:
    """
    Select leverage based on asset volatility measured at scan time.
    Higher ATR% = more volatile = lower leverage.
    Low-liq assets are capped at LOW_LIQ_LEVERAGE regardless.
    """
    if atr_stop_pct is None:
        return p_bot.LEVERAGE  # fallback to config default

    # vol spike modifier: spiking volume = more slippage risk = be conservative
    spike_adj = 5 if vol_spike >= 3.0 else (2 if vol_spike >= 2.0 else 0)
    effective_atr = atr_stop_pct + spike_adj

    if effective_atr >= LEV_ATR_V_HIGH:
        lev = 5
    elif effective_atr >= LEV_ATR_HIGH:
        lev = 10
    elif effective_atr >= LEV_ATR_MID:
        lev = 15
    elif effective_atr >= LEV_ATR_LOW:
        lev = 20
    else:
        lev = 30

    if is_low_liq:
        return min(lev, p_bot.LOW_LIQ_LEVERAGE)  # never exceed low-liq ceiling
    return lev

# Telegram
TG_CHAT_ID          = os.getenv("TG_CHAT_ID", "")
TG_BOT_TOKEN        = os.getenv("TG_BOT_TOKEN", "")

# Fast-track entry: fire immediately when score exceeds threshold
FAST_TRACK_SCORE            = pc.SCORE_FAST_TRACK
FAST_TRACK_COOLDOWN_SECONDS = 300   # seconds before same symbol can fast-track again
RESULT_STALENESS_SECONDS    = 120   # discard scan results older than this

# Per-symbol re-entry cooldown (4 candles × 4H = 16 hours)
COOLDOWN_SECONDS = 4 * 4 * 3600

# Exit signal configuration
EXIT_SIGNAL_SCORE_THRESHOLD = 100
EXIT_SIGNAL_SCAN_INTERVAL    = 60   # seconds between opposite signal checks
LAST_EXIT_SCAN_TIME: Dict[str, float] = {}
# ── System Thresholds & Parameters ──────────────────────────────────────────
# Moved from hardcoded logic for better tuning and observability.

# Leverage Scaling (pick_sim_leverage)
LEV_ATR_V_HIGH = float(os.getenv("LEV_ATR_V_HIGH", "4.0"))   # -> 5x
LEV_ATR_HIGH   = float(os.getenv("LEV_ATR_HIGH", "2.5"))     # -> 10x
LEV_ATR_MID    = float(os.getenv("LEV_ATR_MID", "1.5"))      # -> 15x
LEV_ATR_LOW    = float(os.getenv("LEV_ATR_LOW", "0.8"))      # -> 20x

# Hawkes Cluster Throttling (_get_cluster_threshold_penalty)
HAWKES_INTENSITY_CRITICAL = float(os.getenv("HAWKES_CRITICAL", "3.0")) # -> +50 penalty
HAWKES_INTENSITY_HIGH     = float(os.getenv("HAWKES_HIGH", "2.0"))     # -> +30 penalty
HAWKES_INTENSITY_MID      = float(os.getenv("HAWKES_MID", "1.2"))      # -> +15 penalty

# Entropy Deflator Parameters
ENTROPY_MAX_PENALTY   = int(os.getenv("ENTROPY_MAX_PENALTY", "35"))
ENTROPY_SAT_WEIGHT    = int(os.getenv("ENTROPY_SAT_WEIGHT", "30"))
ENTROPY_SAT_CAP       = int(os.getenv("ENTROPY_SAT_CAP", "25"))
ENTROPY_IMB_WEIGHT    = int(os.getenv("ENTROPY_IMB_WEIGHT", "15"))
ENTROPY_ALERT_LEVEL   = int(os.getenv("ENTROPY_ALERT_LEVEL", "15"))

# ─────────────────────────────────────────────────────────────────────────────
# Global State Management
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimBotState:
    """Consolidated thread-safe state for the Simulation Bot."""
    # Paper Account State (In-memory cache)
    balance: float = INITIAL_BALANCE
    positions: List[Dict[str, Any]] = field(default_factory=list)
    
    # Market Data
    live_prices: Dict[str, float] = field(default_factory=dict)
    
    # Cooldowns & Throttling
    last_exit_times: Dict[str, float] = field(default_factory=dict)
    fast_track_cooldowns: Dict[str, float] = field(default_factory=dict)
    fast_track_opened: set[str] = field(default_factory=set)
    
    # Analytics & Stats
    rolling_stats: Dict[str, Any] = field(default_factory=lambda: {"wins": 0, "losses": 0, "win_pnl": 0.0, "loss_pnl": 0.0})
    equity_history: List[float] = field(default_factory=list)
    pnl_histories: Dict[str, List[float]] = field(default_factory=dict)
    entropy_penalty: int = 0
    
    # System Control
    is_running: bool = True
    display_thread_running: bool = False
    ws_app: Optional[websocket.WebSocketApp] = None
    ws_thread: Optional[threading.Thread] = None
    
    # Events
    slot_available_event: threading.Event = field(default_factory=threading.Event)
    display_paused_event: threading.Event = field(default_factory=threading.Event)
    
    # Locks
    lock: threading.RLock = field(default_factory=threading.RLock)
    file_io_lock: threading.Lock = field(default_factory=threading.Lock)

    def load_account(self):
        """Loads paper account from disk into memory."""
        with self.file_io_lock:
            if not PAPER_ACCOUNT_FILE.exists():
                self.save_account()
                return

            for attempt in range(5):
                try:
                    content = PAPER_ACCOUNT_FILE.read_text()
                    if not content: continue
                    data = json.loads(content)
                    with self.lock:
                        self.balance = float(data.get("balance", INITIAL_BALANCE))
                        self.positions = data.get("positions", [])
                    return
                except (json.JSONDecodeError, ValueError, OSError):
                    time.sleep(0.05 * (attempt + 1))

    def save_account(self):
        """Flushes in-memory account state to disk."""
        with self.file_io_lock:
            with self.lock:
                data = {"balance": self.balance, "positions": self.positions}
            try:
                temp_file = PAPER_ACCOUNT_FILE.with_suffix(".tmp")
                temp_file.write_text(json.dumps(data, indent=2))
                temp_file.replace(PAPER_ACCOUNT_FILE)
            except Exception as e:
                logging.getLogger("sim_bot").error(f"Failed to save paper account: {e}")

    def update_price(self, symbol: str, price: float):
        """Thread-safe update of live prices."""
        with self.lock:
            self.live_prices[symbol] = price

    def get_price(self, symbol: str) -> Optional[float]:
        """Thread-safe retrieval of live price."""
        with self.lock:
            return self.live_prices.get(symbol)

# Instantiate global state
state = SimBotState()

# Unicode Block Elements U+2581–U+2588 (8 chars; index math in sparkline() depends on count=8)
_SPARK_CHARS = "▁▂▃▄▅▆▇█"

# TUI log buffer
_bot_logs: deque[str] = deque(maxlen=100)

# Braille dot patterns for 2x4 resolution per cell
BRAILLE_MAP = [
    [0x01, 0x08],
    [0x02, 0x10],
    [0x04, 0x20],
    [0x40, 0x80],
]

def _to_braille(left_row: int, right_row: int) -> str:
    """Convert two column bit patterns into a braille unicode char."""
    bits = 0
    for row in range(4):
        if left_row & (1 << row):  bits |= BRAILLE_MAP[row][0]
        if right_row & (1 << row): bits |= BRAILLE_MAP[row][1]
    return chr(0x2800 + bits)

def render_pnl_chart(
    pnl_history: list,      # list of floats, e.g. [-0.5, -0.3, 0.1, 0.4, 0.8]
    width: int  = 40,       # character width of chart
    height: int = 8,        # character height of chart
    label: str  = "",       # e.g. "ENAUSDT"
    term  = None,           # blessed terminal instance
    y: int = 0,             # screen row to render at
    x: int = 0,             # screen col to render at
) -> list:
    """
    Renders a smooth braille PnL line chart.
    Returns list of strings (one per row) — print them or pass term for positioned render.
    """
    if not pnl_history:
        pnl_history = [0.0]

    # Pad or trim to fit width*2 data points (2 per char cell)
    points = pnl_history[-(width * 2):]
    while len(points) < width * 2:
        points = [points[0]] * (width * 2 - len(points)) + points

    lo  = min(points)
    hi  = max(points)
    span = (hi - lo) or 1e-10
    rows = height * 4  # braille gives 4 vertical dots per char row

    # Map each data point to a row index 0..rows-1
    def to_row(v):
        return int((v - lo) / span * (rows - 1))

    scaled = [to_row(p) for p in points]

    # Build the 2D braille grid
    grid = [[[0, 0] for _ in range(width)] for _ in range(height)]

    for col_idx in range(width):
        left_val  = scaled[col_idx * 2]
        right_val = scaled[col_idx * 2 + 1]

        for val, side in [(left_val, 0), (right_val, 1)]:
            char_row  = height - 1 - (val // 4)
            dot_row   = val % 4
            char_row  = max(0, min(height - 1, char_row))
            grid[char_row][col_idx][side] |= (1 << dot_row)

    # Render rows into strings
    zero_char_row = height - 1 - (to_row(0.0) // 4)
    lines = []

    for row_idx in range(height):
        line = ""
        for col_idx in range(width):
            l, r = grid[row_idx][col_idx]
            line += _to_braille(l, r)
        lines.append(line)

    current_pnl = pnl_history[-1]
    if term:
        chart_color  = term.bright_green if current_pnl >= 0 else term.red
        zero_color   = term.yellow
        label_color  = term.cyan
        reset        = term.normal
    else:
        chart_color  = Fore.LIGHTGREEN_EX if current_pnl >= 0 else Fore.RED
        zero_color   = Fore.YELLOW
        label_color  = Fore.CYAN
        reset        = Style.RESET_ALL

    output_lines = []

    # Top label bar
    pnl_str  = f"{current_pnl:+.4f} USDT"
    hi_str   = f"▲ {hi:+.4f}"
    lo_str   = f"▼ {lo:+.4f}"
    top_bar  = f"{label:<14} {pnl_str:>14}  {hi_str}  {lo_str}"

    output_lines.append(label_color + top_bar + reset)

    # Chart rows
    for row_idx, line in enumerate(lines):
        prefix = "│"
        suffix = "│"

        if row_idx == zero_char_row:
            output_lines.append(
                zero_color + prefix + reset +
                chart_color + line + reset +
                zero_color + suffix + reset +
                f" 0.00"
            )
        else:
            output_lines.append(zero_color + prefix + reset + chart_color + line + reset + zero_color + suffix + reset)

    # Bottom axis
    axis = "└" + "─" * width + "┘"
    time_label = "  entry" + " " * (width - 14) + "now  "
    output_lines.append(zero_color + axis + reset)
    output_lines.append(label_color + time_label + reset)

    # Render to screen if term provided
    if term:
        for i, l_content in enumerate(output_lines):
            print(term.move_xy(x, y + i) + l_content)
    else:
        for l_content in output_lines:
            print(l_content)

    return output_lines

def update_pnl_history(symbol: str, current_pnl: float):
    """Adds a new PnL data point to the history for the given symbol."""
    with state.lock:
        if symbol not in state.pnl_histories:
            state.pnl_histories[symbol] = []
        state.pnl_histories[symbol].append(current_pnl)
        # Keep last 200 data points
        state.pnl_histories[symbol] = state.pnl_histories[symbol][-200:]

# ── Logging Setup ─────────────────────────────────────────────────────
# Use the shared colored logging setup from phemex_common with buffer capture
logger = pc.setup_colored_logging(
    "sim_bot",
    level=logging.INFO,
    log_file=Path(SCRIPT_DIR) / "sim_bot.log",
    buffer=_bot_logs
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def tui_log(msg: str, event_type: str = "SIM") -> None:
    """Logs a message to both the system audit log and the TUI buffer."""
    pc.log_system_event(event_type, msg)
    # Ensure it also goes into our local logger which is hooked to the TUI deque
    logger.info(msg)


def play_animation(anim_fn):
    """Safely plays a cinematic animation by pausing the TUI thread."""
    state.display_paused_event.set()
    time.sleep(0.5) # Let TUI finish its last frame
    animations.clear()
    try:
        anim_fn()
    finally:
        animations.clear()
        state.display_paused_event.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────────────

def send_telegram_message(message: str) -> None:
    """Sends a message to the configured Telegram chat."""
    try:
        url     = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Paper Account Management
# ─────────────────────────────────────────────────────────────────────────────

def load_paper_account() -> dict:
    """Loads the paper account, using the in-memory state if available."""
    # Ensure memory is populated at least once (or just return memory)
    # We will use the class methods directly in the main logic later, 
    # but for compatibility during migration:
    with state.lock:
        return {"balance": state.balance, "positions": state.positions}


def save_paper_account(data: dict) -> None:
    """Persists the current paper account state to disk from memory."""
    with state.lock:
        state.balance = data.get("balance", state.balance)
        state.positions = data.get("positions", state.positions)
    
    # Save outside the lock to maintain order (file_io_lock -> lock)
    state.save_account()


def _close_all_positions() -> None:
    """Manually closes every active paper position at the current market price."""
    # REF: Tier 3: Non-Descriptive Variable Naming (acc -> account)
    account = load_paper_account()
    if not account["positions"]:
        print(Fore.YELLOW + "  No positions to close.")
        return

    print(Fore.CYAN + f"  Closing {len(account['positions'])} positions...")

    # --- Kill Cinematic ---
    play_animation(animations.kill)

    for pos in account["positions"]:
        symbol = pos["symbol"]
        side   = pos["side"]
        entry  = pos["entry"]
        size   = float(pos["size"])

        now = state.get_price(symbol)

        if now is None:
            try:
                ticker = pc.get_tickers()
                now = next((float(t["lastRp"]) for t in ticker if t["symbol"] == symbol), entry)
            except Exception:
                now = entry

        pnl = (now - entry) * size if side == "Buy" else (entry - now) * size
        account["balance"] += (pos.get("margin", 0.0) + pnl)

        with state.lock:
            state.last_exit_times[symbol] = time.time()

        # Standardize on timezone-aware UTC for JSON storage while keeping logs human-readable
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        pnl_emoji = "✅" if pnl > 0 else "❌"
        send_telegram_message(
            f"⏹ *SIM TRADES MANUALLY CLOSED (V2)*\n\n"
            f"*Symbol:* {symbol}\n"
            f"*Side:* {side}\n"
            f"*Exit Price:* {now}\n"
            f"*PnL:* {pnl_emoji} {pnl:+.4f} USDT\n"
            f"*Time:* {now_utc.strftime('%H:%M:%S')}"
        )

        _log_closed_trade(
            symbol, side, entry, now, size,
            pos.get("entry_score", 0), pos.get("entry_time"), "manual_all_v2"
        )
        print(Fore.GREEN + f"  Closed {symbol} at {now}")

    state.positions = []
    state.save_account()
    save_sim_cooldowns()
    state.slot_available_event.set()
    print(Fore.GREEN + Style.BRIGHT + "  All positions closed successfully.")


def save_sim_cooldowns() -> None:
    """Persists active re-entry and fast-track cooldowns to disk, pruning expired entries."""
    with state.lock:
        active_exit = {s: ts for s, ts in state.last_exit_times.items() if time.time() - ts < COOLDOWN_SECONDS}
        active_ft   = {s: ts for s, ts in state.fast_track_cooldowns.items() if time.time() - ts < FAST_TRACK_COOLDOWN_SECONDS}

    data = {
        "last_exit": active_exit,
        "fast_track": active_ft
    }
    try:
        SIM_COOLDOWN_FILE.write_text(json.dumps(data))
    except OSError:
        logger.error("Failed to save simulation cooldowns.")


def load_sim_cooldowns() -> None:
    """Loads re-entry and fast-track cooldowns from disk and discards any that have expired."""
    if not SIM_COOLDOWN_FILE.exists():
        return
    try:
        data = json.loads(SIM_COOLDOWN_FILE.read_text())
        if isinstance(data, dict) and "last_exit" in data and "fast_track" in data:
            exit_data = data["last_exit"]
            ft_data   = data["fast_track"]
        else:
            exit_data = data
            ft_data   = {}

        with state.lock:
            state.last_exit_times = {
                s: float(ts) for s, ts in exit_data.items()
                if time.time() - float(ts) < COOLDOWN_SECONDS
            }
            state.fast_track_cooldowns = {
                s: float(ts) for s, ts in ft_data.items()
                if time.time() - float(ts) < FAST_TRACK_COOLDOWN_SECONDS
            }
        logger.info(f"Loaded {len(state.last_exit_times)} exit and {len(state.fast_track_cooldowns)} fast-track cooldowns.")
    except (json.JSONDecodeError, ValueError, AttributeError):
        logger.error("Failed to load simulation cooldowns — JSON is invalid.")


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket & Live Price Feed
# ─────────────────────────────────────────────────────────────────────────────

def _ws_on_message(ws: websocket.WebSocketApp, message: str) -> None:
    """Handles inbound WebSocket messages and updates the live price cache."""
    try:
        data = json.loads(message)
        if "market24h_p" in data:
            tick   = data["market24h_p"]
            symbol = tick.get("symbol")
            close  = tick.get("closeRp")
            if symbol and close is not None:
                state.update_price(symbol, float(close))
                _check_stops_live(symbol)
    except json.JSONDecodeError as e:
        logger.debug(f"WS message parse error: {e}")


def _ws_on_open(ws: websocket.WebSocketApp) -> None:
    """Subscribes to all currently open positions on WebSocket connect."""
    logger.info("WebSocket connection opened.")
    # REF: Tier 3: Non-Descriptive Variable Naming (acc -> account)
    account = load_paper_account()
    symbols = [p["symbol"] for p in account.get("positions", [])]
    if symbols:
        ws.send(json.dumps({"id": 1, "method": "market24h_p.subscribe", "params": symbols}))


def _ws_heartbeat(ws: websocket.WebSocketApp, stop_event: threading.Event) -> None:
    """Keeps the WebSocket alive by sending periodic pings."""
    while not stop_event.is_set():
        time.sleep(5)
        # Check if this heartbeat instance is still the active one
        if ws is not state.ws_app:
            logger.debug("Heartbeat thread detected stale WS app — exiting.")
            break
        try:
            if ws.sock and ws.sock.connected:
                ws.send(json.dumps({"id": 0, "method": "server.ping", "params": []}))
            else:
                # Exit if socket is no longer connected
                break
        except (websocket.WebSocketConnectionClosedException, BrokenPipeError):
            logger.debug("WebSocket closed during heartbeat — exiting heartbeat thread.")
            break
        except Exception as e:
            logger.debug(f"Heartbeat error: {e}")
            break


def _ws_run_loop() -> None:
    """Maintains the WebSocket connection, reconnecting while positions are open."""
    ws_url = "wss://testnet.phemex.com/ws" if "testnet" in pc.BASE_URL else "wss://ws.phemex.com"

    retries = 0
    while True:
        stop_event = threading.Event()
        state.ws_app = websocket.WebSocketApp(ws_url, on_message=_ws_on_message, on_open=_ws_on_open)
        threading.Thread(target=_ws_heartbeat, args=(state.ws_app, stop_event), daemon=True).start()
        state.ws_app.run_forever()

        # Signal heartbeat to stop after run_forever exits
        stop_event.set()

        # Grace period to allow pending saves/subscriptions to complete
        time.sleep(2.0)
        if not load_paper_account().get("positions"):
            break

        retries += 1
        delay = min(2**retries, 60)
        logger.info(f"WebSocket disconnected. Retrying in {delay}s (attempt {retries})...")
        time.sleep(delay)


def _ensure_ws_started() -> None:
    """Starts the WebSocket thread if it is not already running."""
    if state.ws_thread is None or not state.ws_thread.is_alive():
        state.ws_thread = threading.Thread(target=_ws_run_loop, daemon=True)
        state.ws_thread.start()


def _subscribe_symbol(symbol: str) -> None:
    """Subscribes the WebSocket to a new symbol after a short delay."""
    def _do_sub() -> None:
        time.sleep(1.5)
        if state.ws_app and state.ws_app.sock and state.ws_app.sock.connected:
            with state.lock:
                symbols = [p["symbol"] for p in state.positions]
            state.ws_app.send(json.dumps({"id": 1, "method": "market24h_p.subscribe", "params": symbols}))

    threading.Thread(target=_do_sub, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Stop / Take-Profit Monitoring
# ─────────────────────────────────────────────────────────────────────────────

def _check_stops_live(symbol: str) -> None:
    """Evaluates trailing-stop and take-profit levels for a symbol on each price tick."""
    # Narrow lock scope — copy data then release lock
    exit_to_process = None
    with state.lock:
        positions = state.positions
        pos_idx   = next((i for i, p in enumerate(positions) if p["symbol"] == symbol), None)
        if pos_idx is None:
            return

        pos = positions[pos_idx]
        current_price = state.live_prices.get(symbol)
        if current_price is None:
            return

        side  = pos["side"]
        entry = pos["entry"]
        size  = float(pos["size"])
        # Use .get() for stop_price and check existence
        stop_price = pos.get("stop_price")
        if stop_price is None:
            return

        stop_hit   = False
        tp_hit     = False
        exit_price = current_price

        if side == "Buy":
            if current_price > pos.get("high_water", 0.0):
                pos["high_water"]  = current_price
                pos["stop_price"]  = current_price * (1.0 - p_bot.TRAIL_PCT)
            if current_price <= stop_price:
                stop_hit   = True
                exit_price = stop_price
            elif "take_profit" in pos and current_price >= pos["take_profit"]:
                tp_hit     = True
                exit_price = pos["take_profit"]
        else:
            if current_price < pos.get("low_water", 9_999_999.0):
                pos["low_water"]  = current_price
                pos["stop_price"] = current_price * (1.0 + p_bot.TRAIL_PCT)
            if current_price >= stop_price:
                stop_hit   = True
                exit_price = stop_price
            elif "take_profit" in pos and current_price <= pos["take_profit"]:
                tp_hit     = True
                exit_price = pos["take_profit"]

        if not (stop_hit or tp_hit):
            # Move outside the lock to maintain order (file_io_lock -> lock)
            pass
        else:
            exit_reason = "Stop Hit" if stop_hit else "Take Profit Hit"
            pnl = (exit_price - entry) * size if side == "Buy" else (entry - exit_price) * size
            
            # Prepare for I/O outside the lock
            exit_to_process = {
                "symbol": symbol,
                "side": side,
                "exit_reason": exit_reason,
                "exit_price": exit_price,
                "pnl": pnl,
                "entry": entry,
                "size": size,
                "entry_score": pos.get("entry_score", 0),
                "entry_time": pos.get("entry_time"),
                "stop_hit": stop_hit
            }

            # Update in-memory state
            state.balance += (pos.get("margin", 0.0) + pnl)
            state.positions.pop(pos_idx)
            state.last_exit_times[symbol] = time.time()
        
    # Process I/O outside the lock
    state.save_account()

    if exit_to_process:
        save_sim_cooldowns()
        state.slot_available_event.set()

        tui_log(f"{exit_to_process['exit_reason'].upper()} HIT: {symbol} {exit_to_process['side']} closed at {exit_to_process['exit_price']}", event_type="EXIT")

        pnl_emoji = "✅" if exit_to_process["pnl"] > 0 else "❌"

        # --- Exit Cinematic ---
        if exit_to_process["pnl"] > 10.0:  # Big win threshold
            play_animation(animations.big_win)
        elif exit_to_process["pnl"] > 0:
            play_animation(animations.win)
        else:
            play_animation(animations.loss)

        # Duration
        hold_time = 0
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if exit_to_process["entry_time"]:
            try:
                entry_time_dt = datetime.datetime.fromisoformat(exit_to_process["entry_time"])
                if entry_time_dt.tzinfo is None:
                    entry_time_dt = entry_time_dt.replace(tzinfo=datetime.timezone.utc)
                hold_time = (now_utc - entry_time_dt).total_seconds()
            except Exception as e:
                logger.warning(f"Failed to parse entry time for {symbol}: {e}")
        h_min, h_sec = divmod(int(hold_time), 60)
        h_hour, h_min = divmod(h_min, 60)
        dur_str = f"{h_hour}h {h_min}m" if h_hour > 0 else (f"{h_min}m {h_sec}s" if h_min > 0 else f"{h_sec}s")

        send_telegram_message(
            f"🔔 *SIM TRADE CLOSED ({exit_to_process['exit_reason']})*\n\n"
            f"*Symbol:* {symbol}\n"
            f"*Side:* {exit_to_process['side']}\n"
            f"*Exit Price:* {exit_to_process['exit_price']}\n"
            f"*PnL:* {pnl_emoji} {exit_to_process['pnl']:+.4f} USDT\n"
            f"*Duration:* {dur_str}\n"
            f"*Time:* {now_utc.strftime('%H:%M:%S')}"
        )
        _log_closed_trade(
            symbol, exit_to_process["side"], exit_to_process["entry"],
            exit_to_process["exit_price"], exit_to_process["size"],
            exit_to_process["entry_score"], exit_to_process["entry_time"],
            "stop" if exit_to_process["stop_hit"] else "tp"
        )


def _log_closed_trade(
    symbol: str,
    direction: str,
    entry: float,
    exit_price: float,
    size: float,
    entry_score: float,
    entry_time: Optional[str],
    reason: str,
    signals: Optional[List[str]] = None,
    slippage: float = 0.0,
) -> None:
    """Appends a closed-trade record to sim_trade_results.json."""
    results_file = SCRIPT_DIR / "sim_trade_results.json"
    pnl = (exit_price - entry) * size if direction == "Buy" else (entry - exit_price) * size

    hold_time = 0
    if entry_time:
        try:
                # REF: Tier 3: Temporal Inconsistency
                entry_time_dt = datetime.datetime.fromisoformat(entry_time)
                if entry_time_dt.tzinfo is None:
                    entry_time_dt = entry_time_dt.replace(tzinfo=datetime.timezone.utc)
                hold_time = (datetime.datetime.now(datetime.timezone.utc) - entry_time_dt).total_seconds()
        except ValueError:
            logger.error("Invalid entry_time format — using zero hold time.")

    # Standardize on timezone-aware UTC for JSON storage
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "symbol":      symbol,
        "direction":   "LONG" if direction == "Buy" else "SHORT",
        "entry":       entry,
        "exit":        exit_price,
        "pnl":         round(pnl, 4),
        "hold_time_s": int(hold_time),
        "score":       entry_score,
        "reason":      reason,
        "timestamp":   now_utc.isoformat(), # REF: Tier 3: Temporal Inconsistency
        "signals":     signals or [],
        "slippage":    round(slippage, 8),
    }

    history: List[dict] = []
    with state.file_io_lock:
        if results_file.exists():
            try:
                history = json.loads(results_file.read_text())
            except (json.JSONDecodeError, OSError):
                logger.error("Failed to read trade history — starting fresh.")
        history.append(record)
        results_file.write_text(json.dumps(history, indent=2))

    # ── Upgrade #8: Signal analytics recording ────────────────────────────────
    if _ANALYTICS_OK:
        try:
            analytics.record_trade(
                signal_types = record.get("signals", ["UNKNOWN"]),
                entry_price  = entry,
                exit_price   = exit_price,
                pnl          = pnl,
                direction    = record["direction"],
                symbol       = symbol,
            )
        except Exception as analytics_err:
            logger.debug(f"signal_analytics record error: {analytics_err}")

    # --- Update rolling stats for Kelly ---
    with state.lock:
        if pnl > 0:
            state.rolling_stats["wins"] += 1
            state.rolling_stats["win_pnl"] += pnl
        else:
            state.rolling_stats["losses"] += 1
            state.rolling_stats["loss_pnl"] += pnl

    # ── Upgrade #7: Notify drawdown guard ─────────────────────────────────────
    if _DD_OK:
        with state.lock:
            current_bal = state.balance
        drawdown_guard.record_pnl(pnl, current_balance=current_bal)

    # ── Upgrade #12: Notify risk manager ─────────────────────────────────────
    if _RISK_MGR_OK:
        risk_mgr.record_trade_result(pnl)

    m, s = divmod(int(hold_time), 60)
    tui_log(
        f"CLOSED {symbol} | Entry: {entry}  Exit: {exit_price} | "
        f"PnL: {pnl:+.4f} USDT | Held: {m}m {s}s | Score: {entry_score}",
        event_type="HISTORY"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TUI — Drawing Helpers  (v2)
# ─────────────────────────────────────────────────────────────────────────────

# ── String helpers ────────────────────────────────────────────────────────────

def _vlen(s: str) -> int:
    """Visible length of a string — strips ANSI/escape codes."""
    return len(re.sub(r"\x1b\[[0-9;]*[mKHJ]", "", s))


def _rpad(s: str, width: int, char: str = " ") -> str:
    """Right-pad a styled string to exact visible `width`."""
    return s + char * max(0, width - _vlen(s))


# ── Box/panel primitives ──────────────────────────────────────────────────────

def _box_top(title: str, width: int, right_tag: str = "") -> str:
    """Single-line top border: ┌─ TITLE ──────────── right_tag ┐"""
    inner = width - 2
    if title:
        lbl = f"─ {title} "
        if right_tag:
            gap = inner - _vlen(lbl) - _vlen(right_tag) - 1
            return f"┌{lbl}{'─' * max(gap, 1)}{right_tag}┐"
        return f"┌{lbl}{'─' * (inner - _vlen(lbl))}┐"
    return f"┌{'─' * inner}┐"


def _box_bot(width: int) -> str:
    return f"└{'─' * (width - 2)}┘"


def _box_row(term: blessed.Terminal, content: str, width: int) -> str:
    """│ content (padded) │  — content is already styled."""
    inner = width - 4
    padded = _rpad(content, inner)
    return term.cyan("│") + " " + padded + term.normal + " " + term.cyan("│")


def _box_empty(term: blessed.Terminal, width: int) -> str:
    return term.cyan("│") + " " * (width - 2) + term.cyan("│")


# ── Sparkline ─────────────────────────────────────────────────────────────────

def sparkline(data: List[float], width: int) -> str:
    """Returns a unicode sparkline of `width` characters from the given data."""
    if not data:
        return "▁" * width
    data = data[-width:]
    lo, hi = min(data), max(data)
    rng = hi - lo if hi != lo else 1.0
    return "".join(_SPARK_CHARS[min(int((v - lo) / rng * 7), 7)] for v in data)


# ── Header ────────────────────────────────────────────────────────────────────

def _draw_header(term: blessed.Terminal, current_time: str, max_width: int = 80) -> None:
    """Draws the top header: double outer box, title left, clock right."""
    w = max_width
    title     = "PHEMEX SIM BOT"
    badge     = "◈ PAPER"
    # Raw visible widths for gap calculation
    left_raw  = f"  ⚡ {title}  {badge}"
    right_raw = f"{current_time}  "
    gap       = max(0, w - 2 - len(left_raw) - len(right_raw))

    left_styled  = (
        "  ⚡ "
        + term.bold_cyan(title)
        + "  "
        + term.yellow(badge)
    )
    right_styled = term.bold_white(current_time) + "  "
    body = term.cyan("║") + left_styled + " " * gap + right_styled + term.cyan("║")

    print(term.move_xy(2, 1) + term.cyan("╔" + "═" * (w - 2) + "╗"))
    print(term.move_xy(2, 2) + body)
    print(term.move_xy(2, 3) + term.cyan("╠" + "═" * (w - 2) + "╣"))


# ── Positions ─────────────────────────────────────────────────────────────────

def _draw_positions_section(
    term: blessed.Terminal,
    positions: List[Dict[str, Any]],
    current_prices: Dict[str, float],
    start_row: int,
    max_width: int = 80,
) -> int:
    """Renders the active-positions panel."""
    w   = max_width
    row = start_row
    n   = len(positions)

    slot_tag = f"─ {n} open " if n else "─ idle "
    print(term.move_xy(2, row) + term.cyan(_box_top("OPEN POSITIONS", w, slot_tag)))
    row += 1

    if not positions:
        msg = term.white("  Waiting for qualifying setups") + term.cyan(" ·")
        print(term.move_xy(2, row) + _box_row(term, _rpad(msg, w - 4), w))
        row += 1
    else:
        for pos in positions:
            sym        = pos["symbol"]
            side       = pos["side"]
            entry      = float(pos["entry"])
            size       = float(pos["size"])
            stop       = float(pos.get("stop_price", 0))
            tp         = float(pos.get("take_profit", 0))
            orig_stop  = float(pos.get("original_stop", stop))
            score      = pos.get("entry_score", 0)
            now        = current_prices.get(sym)
            is_long    = side == "Buy"

            upnl = 0.0
            if now:
                upnl = (now - entry) * size if is_long else (entry - now) * size

            # Direction badge
            dir_badge = (
                term.bold_green("▲ LONG ") if is_long else term.bold_red("▼ SHORT")
            )
            now_str = f"{now:.5g}" if now else "·······"
            if now:
                pnl_str = (
                    term.bold_green(f"+{upnl:.4f}")
                    if upnl >= 0 else term.bold_red(f"{upnl:.4f}")
                )
            else:
                pnl_str = term.white("·······")

            margin_s = term.cyan(f"M: ${pos.get('margin', 0.0):.1f}")
            lev_s    = term.yellow(f"L: {pos.get('leverage', '??')}x")
            # ── Row 1: direction · symbol · entry → now · pnl · score ──────
            score_badge = term.yellow(f"[{score}]")
            arrow       = term.white("──▶")
            entry_s     = term.white(f"{entry:.5g}")
            now_s       = term.white(now_str)
            sym_s       = term.bold_white(f"{sym:<12}")

            # Duration
            dur_str = "???"
            entry_time_str = pos.get("entry_time")
            if entry_time_str:
                try:
                    entry_dt = datetime.datetime.fromisoformat(entry_time_str)
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.replace(tzinfo=datetime.timezone.utc)
                    diff = datetime.datetime.now(datetime.timezone.utc) - entry_dt
                    tot_sec = int(diff.total_seconds())
                    if tot_sec < 60: dur_str = f"{tot_sec}s"
                    elif tot_sec < 3600: dur_str = f"{tot_sec//60}m"
                    else: dur_str = f"{tot_sec//3600}h {(tot_sec%3600)//60}m"
                except Exception:
                    pass
            dur_badge = term.white(f"({dur_str})")

            line1 = f" {dir_badge} {sym_s} {entry_s} {arrow} {now_s}  {margin_s}  {lev_s}  {pnl_str}  {score_badge} {dur_badge}"
            print(term.move_xy(2, row) + _box_row(term, line1, w))
            row += 1

            # ── Row 2: price-position bar ────────────────────────────────────
            if now:
                bar_w = w - 16
                pts   = [orig_stop, stop, entry, now, tp]
                lo    = min(pts)
                hi    = max(pts)
                rng   = (hi - lo) if hi != lo else 1.0

                def gp(v: float) -> int:
                    return max(0, min(bar_w - 1, int((v - lo) / rng * (bar_w - 1))))

                bar = list("─" * bar_w)
                bar[gp(orig_stop)] = term.red("╳")
                bar[gp(stop)]      = term.bold_red("S")
                bar[gp(entry)]     = term.yellow("E")
                bar[gp(tp)]        = term.bold_green("T")
                bar[gp(now)]       = term.bold_white("●")

                sl_s  = term.red(f"{stop:.4g}")
                tp_s  = term.green(f"{tp:.4g}")
                label = f"    ╰ SL {sl_s}  TP {tp_s}  " + term.cyan("[") + "".join(bar) + term.cyan("]")
                print(term.move_xy(2, row) + _box_row(term, label, w))
                row += 1

    print(term.move_xy(2, row) + term.cyan(_box_bot(w)))
    row += 1
    return row


# ── Account + Session (two columns) ──────────────────────────────────────────

def _draw_account_session_section(
    term: blessed.Terminal,
    balance: float,
    locked_margin: float,
    current_upnl: float,
    equity: float,
    total_trades: int,
    wins: int,
    losses: int,
    win_rate: float,
    total_closed_pnl: float,
    start_row: int,
    max_width: int = 80,
    equity_history: List[float] = None
) -> int:
    """Two-column panel: wallet left, session stats right."""

    # Use passed history to avoid global mutation side-effects
    spark_data = equity_history if equity_history else []

    w   = max_width
    lw  = 36          # left column width
    gap = 2
    rw  = w - lw - gap

    eq_delta   = equity - INITIAL_BALANCE
    eq_color   = term.bold_green  if eq_delta  >= 0 else term.bold_red
    upnl_color = term.green       if current_upnl >= 0 else term.red
    rpnl_color = term.bold_green  if total_closed_pnl >= 0 else term.bold_red

    # ── Left panel: wallet ────────────────────────────────────────────────────
    left_lines: List[str] = []
    left_lines.append(term.cyan(_box_top("WALLET", lw)))
    left_lines.append(_box_row(term,
        "  Available" + term.bold_white(f"${balance:9.2f}") + term.cyan(" USDT"), lw))
    left_lines.append(_box_row(term,
        "  Locked   " + term.yellow(f"${locked_margin:9.2f}") + term.cyan(" USDT"), lw))
    left_lines.append(_box_row(term,
        "  uPnL     " + upnl_color(f"{current_upnl:+.4f}") + term.cyan(" USDT"), lw))
    left_lines.append(_box_row(term,
        "  Equity   " + eq_color(f"${equity:9.2f}") + term.cyan(" USDT"), lw))
    left_lines.append(term.cyan(_box_bot(lw)))

    # ── Right panel: statistics ───────────────────────────────────────────────
    right_lines: List[str] = []
    right_lines.append(term.cyan(_box_top("STATISTICS", rw)))
    right_lines.append(_box_row(term,
        "  Trades  " + term.bold_white(str(total_trades).ljust(4)), rw))
    right_lines.append(_box_row(term,
        f"  {term.bold_green(f'✅ {wins}W')}   {term.bold_red(f'❌ {losses}L')}"
        f"   Rate {term.yellow(f'{win_rate:.1f}%')}", rw))
    right_lines.append(_box_row(term,
        "  Realized  " + rpnl_color(f"{total_closed_pnl:+.4f}") + term.cyan(" USDT"), rw))
    right_lines.append(_box_empty(term, rw))
    right_lines.append(term.cyan(_box_bot(rw)))

    row = start_row
    for l_line, r_line in zip(left_lines, right_lines):
        print(term.move_xy(2, row) + l_line + " " * gap + r_line)
        row += 1

    return row


# ── Trade history ─────────────────────────────────────────────────────────────

def _draw_history_section(
    term: blessed.Terminal,
    history: List[Dict[str, Any]],
    start_row: int,
    max_width: int = 80,
) -> int:
    """Two-per-row closed trade history (last 6)."""
    w      = max_width
    row    = start_row
    recent = history[::-1][:6]

    print(term.move_xy(2, row) + term.cyan(_box_top("TRADE HISTORY", w)))
    row += 1

    if not recent:
        msg = term.white("  No closed trades yet")
        print(term.move_xy(2, row) + _box_row(term, msg, w))
        row += 1
    else:
        col_w = (w - 6) // 2  # visible width for one trade cell

        def _fmt(t: dict) -> str:
            pnl   = t["pnl"]
            c     = term.bold_green if pnl > 0 else term.bold_red
            badge = "✅" if pnl > 0 else "❌"
            ts    = t["timestamp"][11:16]
            sym   = t["symbol"][:10].ljust(10)
            d     = t["direction"][:5].ljust(5)
            return f" {term.white(ts)} {term.bold_white(sym)} {term.cyan(d)} {badge} {c(f'{pnl:+.4f}')}"

        for i in range(0, len(recent), 2):
            left_cell = _fmt(recent[i])
            if i + 1 < len(recent):
                right_cell = _fmt(recent[i + 1])
                sep        = term.cyan("│")
                content    = _rpad(left_cell, col_w) + sep + right_cell
            else:
                content = left_cell
            print(term.move_xy(2, row) + _box_row(term, content, w))
            row += 1

    print(term.move_xy(2, row) + term.cyan(_box_bot(w)))
    row += 1
    return row

def _draw_equity_chart_section(
    term: blessed.Terminal,
    equity_history: List[float],
    start_row: int,
    max_width: int = 120,
) -> int:
    """Full-width block-bar equity performance chart."""
    w   = max_width
    row = start_row
    h   = 3  # Taller/thicker (3 rows of blocks)

    inner_w = w - 8
    if not equity_history:
        chart_lines = [" " * inner_w] * h
    else:
        # Prepare data
        data = equity_history[-inner_w:]
        while len(data) < inner_w:
            data = ([data[0]] if data else [0.0]) + data

        lo, hi = min(data), max(data)
        rng = hi - lo if hi != lo else 1.0

        chart_lines = []
        # We'll use the _SPARK_CHARS: "▁▂▃▄▅▆▇█"
        # Since it has 8 chars, each row has 8 levels of granularity.
        for r in range(h - 1, -1, -1):
            line = ""
            for v in data:
                # Scale v to 0 .. (h * 8 - 1)
                scaled = int((v - lo) / rng * (h * 8 - 1))
                # Determine how many 'units' are in THIS specific row
                row_units = scaled - (r * 8)

                if row_units >= 7:
                    line += "█"
                elif row_units < 0:
                    line += " "
                else:
                    # Map 0..6 to the spark chars (avoiding the full block █ which is row_units >= 7)
                    line += _SPARK_CHARS[max(0, row_units)]
            chart_lines.append(line)

    print(term.move_xy(2, row) + term.cyan(_box_top("EQUITY PERFORMANCE", w)))
    row += 1

    # Determine color based on trend
    color = term.green if (equity_history and equity_history[-1] >= INITIAL_BALANCE) else term.red

    for line in chart_lines:
        print(term.move_xy(2, row) + _box_row(term, color(line), w))
        row += 1

    print(term.move_xy(2, row) + term.cyan(_box_bot(w)))
    row += 1
    return row

def _draw_consolidated_positions(
    term: blessed.Terminal,
    positions: List[Dict[str, Any]],
    current_prices: Dict[str, float],
    max_width: int = 120,
) -> None:
    """
    Renders open positions at the bottom of the screen with consolidated stats
    and a braille PnL chart inside the box.
    """
    if not positions:
        return

    # Determine start row (bottom-anchored)
    # Each position box is now ~10 rows high with compact chart.
    display_positions = positions[-5:]
    chart_h = 4
    box_h = chart_h + 6

    # Start drawing from roughly the bottom
    start_y = term.height - (len(display_positions) * (box_h + 1)) - 2

    for idx, pos in enumerate(display_positions):
        row = start_y + (idx * (box_h + 1))
        sym        = pos["symbol"]
        side       = pos["side"]
        entry      = float(pos["entry"])
        size       = float(pos["size"])
        stop       = float(pos.get("stop_price", 0))
        tp         = float(pos.get("take_profit", 0))
        margin     = float(pos.get("margin", 0))
        now        = current_prices.get(sym)
        is_long    = side == "Buy"

        hist = state.pnl_histories.get(sym, [0.0])
        upnl = 0.0
        if now:
            upnl = (now - entry) * size if is_long else (entry - now) * size

        # --- Header & Stats line ---
        dir_badge = term.bold_green("▲ LONG") if is_long else term.bold_red("▼ SHORT")
        pnl_color = term.bold_green if upnl >= 0 else term.bold_red

        # Duration
        dur_str = "???"
        entry_time_str = pos.get("entry_time")
        if entry_time_str:
            try:
                entry_dt = datetime.datetime.fromisoformat(entry_time_str)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=datetime.timezone.utc)
                diff = datetime.datetime.now(datetime.timezone.utc) - entry_dt
                tot_sec = int(diff.total_seconds())
                if tot_sec < 60: dur_str = f"{tot_sec}s"
                elif tot_sec < 3600: dur_str = f"{tot_sec//60}m"
                else: dur_str = f"{tot_sec//3600}h {(tot_sec%3600)//60}m"
            except Exception:
                pass
        dur_badge = term.white(f"({dur_str})")

        header = f" {dir_badge} {term.bold_white(sym)}  Entry: {term.white(f'{entry:.5g}')}  Now: {term.white(f'{now:.5g}' if now else '...')}  {dur_badge}"
        stats  = f"    Margin: {term.yellow(f'${margin:.2f}')}  Lev: {term.yellow(f'{pos.get('leverage', '??')}x')}  PnL: {pnl_color(f'{upnl:+.4f}')} USDT"

        print(term.move_xy(2, row) + term.cyan(_box_top("", max_width)))
        print(term.move_xy(4, row) + header)
        row += 1
        print(term.move_xy(2, row) + _box_row(term, stats, max_width))
        row += 1

        # --- Braille Chart Area ---
        chart_w = max_width - 20
        # Use existing chart logic but capture lines
        chart_lines = render_pnl_chart(
            pnl_history=hist,
            width=chart_w,
            height=chart_h,
            label="", # label already in header
            term=None # get strings back
        )

        # Strip the first (label) and last two (axis/labels) lines from render_pnl_chart output
        # since we want to custom integrate them
        core_chart = chart_lines[1:-2]

        for i, line in enumerate(core_chart):
            # If it's the top line of the chart, add "EXITS" indicator
            if i == 0:
                line = line + term.bold_red("  ← EXITS")
            print(term.move_xy(2, row + i) + term.cyan("│ ") + line + term.move_xy(max_width-1, row+i) + term.cyan("│"))

        row += len(core_chart)

        # --- Price Line (Entry/Stop/TP/Now) ---
        bar_w = max_width - 16
        pts   = [stop, entry, tp]
        if now: pts.append(now)
        lo    = min(pts)
        hi    = max(pts)
        rng   = (hi - lo) if hi != lo else 1.0
        def gp(v: float) -> int:
            return max(0, min(bar_w - 1, int((v - lo) / rng * (bar_w - 1))))

        bar = list("─" * bar_w)
        bar[gp(stop)]  = term.red("S")
        bar[gp(entry)] = term.yellow("E")
        bar[gp(tp)]    = term.green("T")
        if now: bar[gp(now)] = term.bold_white("●")

        price_line = term.cyan("[") + "".join(bar) + term.cyan("]")
        print(term.move_xy(2, row) + _box_row(term, f"  Price: {price_line}", max_width))
        row += 1

        print(term.move_xy(2, row) + term.cyan(_box_bot(max_width)))

# ── System log ────────────────────────────────────────────────────────────────

def _draw_system_logs_section(
    term: blessed.Terminal,
    logs: List[str],
    start_row: int,
    max_width: int = 80,
) -> int:
    """Color-coded scrolling log panel (last 6 entries)."""
    w   = max_width
    row = start_row

    print(term.move_xy(2, row) + term.cyan(_box_top("SYSTEM LOG", w)))
    row += 1

    with state.lock:
        # deques don't support slicing, convert to list first
        display_logs = list(logs)[-6:]

    # Always render exactly 6 rows
    while len(display_logs) < 6:
        display_logs.append("")

    for entry in display_logs:
        if not entry:
            print(term.move_xy(2, row) + _box_empty(term, w))
        else:
            # Logs are already formatted with ANSI colors by setup_colored_logging
            print(term.move_xy(2, row) + _box_row(term, entry, w))

        row += 1

    print(term.move_xy(2, row) + term.cyan(_box_bot(w)))
    row += 1
    return row


# ── Footer ────────────────────────────────────────────────────────────────────

def _draw_footer(term: blessed.Terminal, row: int, max_width: int = 80) -> None:
    """Bottom bar with keyboard shortcuts."""
    w          = max_width
    left_raw   = "  [S] Close All  [Q] Quit  "
    right_raw  = "  ⚡ FANCYBOT v2  "
    gap        = max(0, w - 2 - len(left_raw) - len(right_raw))

    left_part  = (
        "  "
        + term.bold_white("[S]") + term.white(" Close All")
        + "  "
        + term.bold_white("[Q]") + term.white(" Quit")
        + "  "
    )
    right_part = "  ⚡ " + term.bold_cyan("FANCYBOT") + term.white(" v2") + "  "
    inner      = left_part + term.cyan("─" * gap) + right_part
    line       = term.cyan("╚═") + inner + term.normal + term.cyan("═╝")
    print(term.move_xy(2, row) + line)


# ─────────────────────────────────────────────────────────────────────────────
# TUI — Main Display Loop
# ─────────────────────────────────────────────────────────────────────────────

def _live_pnl_display() -> None:
    """Full-screen TUI dashboard — runs in a dedicated daemon thread."""
    term         = blessed.Terminal()
    results_file = SCRIPT_DIR / "sim_trade_results.json"

    with term.fullscreen(), term.cbreak(), term.hidden_cursor():
        try:
            # [T1-FIX] Guard loop with existing running flag for clean shutdown
            while state.display_thread_running:
                if state.display_paused_event.is_set():
                    time.sleep(0.5)
                    continue

                with state.lock:
                    balance = state.balance
                    positions = state.positions[:] # Copy
                    live_prices = state.live_prices.copy()

                history: List[dict] = []
                # Still need to read history from file for the totals, 
                # but we could cache this too if needed.
                if results_file.exists():
                    try:
                        history = json.loads(results_file.read_text())
                    except Exception:
                        pass

                wins             = [t for t in history if t["pnl"] > 0]
                losses           = [t for t in history if t["pnl"] <= 0]
                total_trades     = len(history)
                win_rate         = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
                total_closed_pnl = sum(t["pnl"] for t in history)
                current_time     = datetime.datetime.now().strftime("%H:%M:%S")

                current_upnl = 0.0
                locked_margin = 0.0
                for p in positions:
                    locked_margin += p.get("margin", 0.0)
                    now = live_prices.get(p["symbol"])
                    if now:
                        entry = p["entry"]
                        size = float(p["size"])
                        pos_pnl = (now - entry) * size if p["side"] == "Buy" else (entry - now) * size
                        current_upnl += pos_pnl
                        update_pnl_history(p["symbol"], pos_pnl)

                equity = balance + locked_margin + current_upnl

                # Update equity history here (state mutation), strictly outside render function
                with state.lock:
                    state.equity_history.append(equity)
                    if len(state.equity_history) > 50: # _max_history
                        state.equity_history.pop(0)

                max_w = 120
                print(term.clear)
                _draw_header(term, current_time, max_w)

                row = 4
                # Top sections (Account, History, Logs)
                row = _draw_account_session_section(
                    term, balance, locked_margin, current_upnl, equity,
                    total_trades, len(wins), len(losses),
                    win_rate, total_closed_pnl, row, max_w,
                    state.equity_history
                )
                row = _draw_history_section(term, history, row, max_w)
                row = _draw_equity_chart_section(term, state.equity_history, row, max_w)
                row = _draw_system_logs_section(term, _bot_logs, row, max_w)

                # Consolidated Positions at the bottom
                _draw_consolidated_positions(term, positions, live_prices, max_w)

                # Footer fixed at the very bottom line
                _draw_footer(term, term.height - 1, max_w)

                key = term.inkey(timeout=0.8)
                if key.lower() == "s":
                    state.display_paused_event.set()
                    confirm_row = row + 1
                    print(
                        term.move_xy(4, confirm_row)
                        + term.on_red(term.bold_white("  ⚠  CLOSE ALL TRADES?  "))
                        + term.bold_yellow("  (Y / N)  "),
                        end="", flush=True,
                    )
                    if term.inkey().lower() == "y":
                        _close_all_positions()
                        time.sleep(1)
                    state.display_paused_event.clear()
                elif key.lower() == "q":
                    break

        except KeyboardInterrupt:
            pass
        finally:
            with state.lock:
                state.display_thread_running = False


# ─────────────────────────────────────────────────────────────────────────────
# Simulation Overrides
# ─────────────────────────────────────────────────────────────────────────────

def get_sim_balance() -> float:
    """Returns the current wallet balance from the paper account."""
    return load_paper_account().get("balance", 0.0)


def get_sim_positions() -> List[dict]:
    """Returns the list of open paper positions."""
    return load_paper_account().get("positions", [])


def check_opposite_signal(symbol: str, side: str, ticker: Optional[dict] = None) -> Tuple[bool, int]:
    """
    Scans the opposite direction for a symbol to see if a reversal is building.
    Returns (True, score) if score >= EXIT_SIGNAL_SCORE_THRESHOLD.
    """
    global LAST_EXIT_SCAN_TIME
    now = time.time()
    if now - LAST_EXIT_SCAN_TIME.get(symbol, 0) < EXIT_SIGNAL_SCAN_INTERVAL:
        return False, 0

    LAST_EXIT_SCAN_TIME[symbol] = now

    try:
        # Fetch fresh ticker if not provided
        if not ticker:
            tickers = pc.get_tickers()
            ticker = next((t for t in tickers if t["symbol"] == symbol), None)

        if not ticker:
            return False, 0

        # Use the opposite scanner module
        scanner = scanner_short if side == "Buy" else scanner_long

        # Minimal config for quick scan - use 15m to catch reversals faster
        cfg = {
            "TIMEFRAME": "15m",
            "MIN_VOLUME": 0,
            "RATE_LIMIT_RPS": 100.0,
            "CANDLES": 100
        }

        res = scanner.analyse(ticker, cfg, enable_ai=False, enable_entity=False)
        if res and res["score"] >= EXIT_SIGNAL_SCORE_THRESHOLD:
            return True, res["score"]

    except Exception as e:
        logger.debug(f"Error in check_opposite_signal for {symbol}: {e}")

    return False, 0


def update_pnl_and_stops() -> None:
    """
    Polls live prices for all open positions, updates PnL, and evaluates
    trailing-stop and take-profit levels.
    """
    # Initialize outside lock to avoid UnboundLocalError
    ticker_map: Dict[str, Any] = {}
    missing: List[str] = []

    # Narrow lock scope — only hold lock while reading/writing the account structure.
    # Move all I/O (Telegram, Logging) outside the lock.
    with state.lock:
        positions = state.positions
        if not positions:
            return

        # Fetch REST tickers only for symbols not yet in the live-price cache
        missing = [p["symbol"] for p in positions if p["symbol"] not in state.live_prices]
        if missing:
            # Releasing stop lock during ticker fetch to avoid blocking stop evaluations
            pass

    # Fetch tickers for all open positions to provide fresh data for opposite signal checks
    try:
        tickers    = pc.get_tickers()
        ticker_map = {t["symbol"]: t for t in tickers}
    except Exception as e:
        logger.debug(f"Failed to fetch REST tickers in update loop: {e}")

    # To store events for I/O outside the lock
    exits_to_process = []

    with state.lock:
        # Re-derive current positions state to avoid phantom PnL on just-closed symbols
        new_positions: List[dict] = []
        closed_any = False

        for pos in state.positions:
            symbol = pos["symbol"]
            current_price = state.live_prices.get(symbol)

            if current_price is None:
                ticker = ticker_map.get(symbol)
                if ticker:
                    current_price = float(ticker.get("lastRp") or 0.0)

            if not current_price:
                new_positions.append(pos)
                continue

            side = pos["side"]
            exit_reason = None
            exit_price  = 0.0
            pnl         = 0.0

            # Use .get() for stop_price and check existence
            stop_price = pos.get("stop_price")
            if stop_price is None:
                new_positions.append(pos)
                continue

            # ── Upgrade #2: ATR-based trailing stop update ────────────────────────────
            trail_dist = pos.get("trail_dist")
            direction_str = "LONG" if side == "Buy" else "SHORT"

            if trail_dist and trail_dist > 0:
                # Use ATR-based trail via pc.update_atr_trail
                new_stop, new_hw, new_lw = pc.update_atr_trail(
                    current_price  = current_price,
                    stop_price     = stop_price,
                    high_water     = pos.get("high_water") or current_price,
                    low_water      = pos.get("low_water")  or current_price,
                    trail_distance = trail_dist,
                    direction      = direction_str,
                )
                pos["stop_price"] = new_stop
                pos["high_water"] = new_hw
                pos["low_water"]  = new_lw
                stop_price = new_stop
            else:
                # Legacy percentage-based trailing stop
                if side == "Buy":
                    if current_price > pos.get("high_water", 0.0):
                        pos["high_water"] = current_price
                        pos["stop_price"] = current_price * (1.0 - p_bot.TRAIL_PCT)
                        stop_price = pos["stop_price"]
                else:
                    if current_price < pos.get("low_water", 999_999_999.0):
                        pos["low_water"]  = current_price
                        pos["stop_price"] = current_price * (1.0 + p_bot.TRAIL_PCT)
                        stop_price = pos["stop_price"]

            if side == "Buy":
                if current_price <= stop_price:
                    exit_reason, exit_price = "Stop Loss", stop_price
                elif "take_profit" in pos and current_price >= pos["take_profit"]:
                    exit_reason, exit_price = "Take Profit", pos["take_profit"]
                if exit_reason:
                    pnl = (exit_price - pos["entry"]) * pos["size"]
            else:
                if current_price >= stop_price:
                    exit_reason, exit_price = "Stop Loss", stop_price
                elif "take_profit" in pos and current_price <= pos["take_profit"]:
                    exit_reason, exit_price = "Take Profit", pos["take_profit"]
                if exit_reason:
                    pnl = (pos["entry"] - exit_price) * pos["size"]

            # ── Check for opposite signal exit ────────────────────────
            if not exit_reason:
                ticker = ticker_map.get(symbol)
                opp_hit, opp_score = check_opposite_signal(symbol, side, ticker=ticker)
                if opp_hit:
                    exit_reason, exit_price = f"Reversal (Score {opp_score})", current_price
                    pnl = (exit_price - pos["entry"]) * pos["size"] if side == "Buy" else (pos["entry"] - exit_price) * pos["size"]

            if exit_reason:
                # Store the exit data for processing after the lock is released
                exits_to_process.append({
                    "symbol": symbol,
                    "side": side,
                    "exit_reason": exit_reason,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "margin": pos.get("margin", 0.0),
                    "entry": pos["entry"],
                    "size": pos["size"],
                    "entry_score": pos.get("entry_score", 0),
                    "entry_time": pos.get("entry_time"),
                    "signals": pos.get("signals", []),
                    "slippage": pos.get("slippage", 0.0),
                })
                state.balance += (pos.get("margin", 0.0) + pnl)
                state.last_exit_times[symbol] = time.time()
                closed_any = True
                continue

            # Position remains open
            pos["pnl"] = (current_price - pos["entry"]) * pos["size"] if side == "Buy" else (pos["entry"] - current_price) * pos["size"]
            new_positions.append(pos)

        if closed_any:
            state.positions = new_positions
    
    # Save outside the lock to maintain order (file_io_lock -> lock)
    state.save_account()

    # Process I/O (exits) outside the lock to avoid blocking
    for ex in exits_to_process:
        symbol = ex["symbol"]
        save_sim_cooldowns()
        state.slot_available_event.set()

        tui_log(f"{ex['exit_reason'].upper()} HIT: {symbol} closed at {ex['exit_price']}")
        pnl_emoji = "✅" if ex['pnl'] > 0 else "❌"
        # Duration
        hold_time = 0
        if ex.get("entry_time"):
            try:
                # REF: Tier 3: Temporal Inconsistency
                entry_time_dt = datetime.datetime.fromisoformat(ex["entry_time"])
                if entry_time_dt.tzinfo is None:
                    entry_time_dt = entry_time_dt.replace(tzinfo=datetime.timezone.utc)
                hold_time = (datetime.datetime.now(datetime.timezone.utc) - entry_time_dt).total_seconds()
            except Exception:
                pass
        h_min, h_sec = divmod(int(hold_time), 60)
        h_hour, h_min = divmod(h_min, 60)
        dur_str = f"{h_hour}h {h_min}m" if h_hour > 0 else (f"{h_min}m {h_sec}s" if h_min > 0 else f"{h_sec}s")

        send_telegram_message(
            f"🔔 *SIM TRADE CLOSED ({ex['exit_reason']})*\n\n"
            f"*Symbol:* {symbol}\n*Side:* {ex['side']}\n"
            f"*Exit Price:* {ex['exit_price']}\n"
            f"*PnL:* {pnl_emoji} {ex['pnl']:+.4f} USDT\n"
            f"*Duration:* {dur_str}\n"
            f"*Time:* {datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')}"
        )
        _log_closed_trade(
            symbol, ex['side'], ex['entry'], ex['exit_price'], ex['size'],
            ex['entry_score'], ex['entry_time'],
            "stop" if "Stop" in ex['exit_reason'] else "tp",
            signals  = ex.get('signals', []),
            slippage = ex.get('slippage', 0.0),
        )


def verify_sim_candidate(symbol: str, direction: str, original_score: int, wait_seconds: int = 20) -> Optional[dict]:
    """
    Waits, then re-scans a single symbol to verify the signal is still valid for simulation.
    Performs iterative checks to ensure price action isn't moving against the signal.
    """
    steps = 3
    step_wait = wait_seconds / steps
    initial_price = None
    last_result = None

    tui_log(f"VERIFY: {symbol} ({direction}) for {wait_seconds}s...")

    for i in range(steps):
        time.sleep(step_wait)

        # Fetch fresh ticker
        try:
            tickers = pc.get_tickers()
            ticker = next((t for t in tickers if t["symbol"] == symbol), None)
        except Exception as e:
            tui_log(f"FAIL: Error fetching ticker for {symbol}: {e}")
            return None

        if not ticker:
            tui_log(f"FAIL: {symbol} ticker not found during verification.")
            return None

        current_price = float(ticker.get("lastRp") or ticker.get("closeRp") or 0.0)
        if initial_price is None:
            initial_price = current_price

        # Price movement check
        price_change = pc.pct_change(current_price, initial_price)

        if direction == "LONG":
            if price_change < -0.6: # Dropping too much during verification
                tui_log(f"FAIL: {symbol} dropping during verify: {price_change:+.2f}%")
                return None
        else: # SHORT
            if price_change > 0.6: # Pumping too much during verification
                tui_log(f"FAIL: {symbol} pumping during verify: {price_change:+.2f}%")
                return None

        # Re-scan using the appropriate scanner module
        scanner = scanner_long if direction == "LONG" else scanner_short

        # Minimal config for re-scan (using p_bot's constants)
        cfg = {
            "TIMEFRAME": p_bot.TIMEFRAME,
            "MIN_VOLUME": p_bot.MIN_VOLUME,
            "RATE_LIMIT_RPS": p_bot.RATE_LIMIT_RPS,
            "CANDLES": 100
        }

        fresh_result = scanner.analyse(ticker, cfg, enable_ai=False, enable_entity=False)

        if not fresh_result:
            tui_log(f"FAIL: {symbol} no longer qualifies at step {i+1}")
            return None

        fresh_score = fresh_result["score"]

        # Spread check: avoid illiquid assets that may have fake signals
        current_spread = fresh_result.get("spread", 0.0)
        if current_spread is not None and current_spread > 0.25:
            tui_log(f"FAIL: {symbol} spread too high: {current_spread:.2f}%")
            return None

        # RSI Momentum Check: Ensure RSI isn't deep in the "over-exhaustion" zone already
        current_rsi = fresh_result.get("rsi")
        if current_rsi:
            if direction == "LONG" and current_rsi > 70:
                tui_log(f"FAIL: {symbol} RSI {current_rsi:.1f} — overbought after wait.")
                return None
            elif direction == "SHORT" and current_rsi < 30:
                tui_log(f"FAIL: {symbol} RSI {current_rsi:.1f} — oversold after wait.")
                return None

        # Allow 15% score degradation during the iterative check
        if fresh_score < original_score * 0.85:
            tui_log(f"FAIL: {symbol} score dropped: {original_score} -> {fresh_score}")
            return None

        last_result = fresh_result
        tui_log(f"  Step {i+1}/{steps}: {symbol} score {fresh_score} ({price_change:+.2f}%)")

    # Final overextension check - avoid chasing if it moved too far in our direction too fast
    final_change = pc.pct_change(last_result["price"], initial_price)
    if abs(final_change) > 1.5:
        tui_log(f"FAIL: {symbol} overextended ({final_change:+.2f}%) during verify.")
        return None

    tui_log(f"VERIFIED: {symbol} score {last_result['score']} — ready for SIM entry.")
    return last_result

def execute_sim_setup(result: dict, direction: str) -> bool:
    """
    Opens a new simulated position from a scanner result.
    Returns True on success, False if the trade is skipped.

    Upgrades integrated:
      #1  Realistic slippage on fill price
      #2  ATR-based stop-loss and trailing stop distance
      #3  Spread filter (skips illiquid conditions)
      #5  MAX_POSITIONS concurrent limit
      #6  Volatility filter (skips low-ATR/price setups)
      #7  Daily drawdown kill switch
      #8  Signal analytics recording on entry
      #12 Dynamic/adaptive risk manager for position sizing
      #13 Telegram halt check
    """
    symbol = result["inst_id"]
    price  = result["price"]
    score  = result["score"]

    # ── Upgrade #7: Daily drawdown kill switch ────────────────────────────────
    if _DD_OK:
        allowed, dd_reason = drawdown_guard.can_open_trade(current_balance=state.balance)
        if not allowed:
            tui_log(f"KILL SWITCH: {symbol} blocked — {dd_reason}", event_type="SKIP")
            return False

    # ── Upgrade #13: Telegram manual halt ────────────────────────────────────
    if _TG_OK and telegram.is_halted():
        tui_log(f"TG HALT: {symbol} blocked — /stop was issued via Telegram", event_type="SKIP")
        return False

    # ── Upgrade #3: Spread filter ─────────────────────────────────────────────
    spread_pct = result.get("spread")
    spread_ok, spread_reason = pc.check_spread_filter(spread_pct, symbol)
    if not spread_ok:
        tui_log(f"SPREAD FILTER: {symbol} SKIP — {spread_reason}", event_type="SKIP")
        return False

    # ── Upgrade #6: Volatility filter ────────────────────────────────────────
    atr_stop_pct = result.get("atr_stop_pct")
    raw_atr      = None
    if atr_stop_pct and price > 0:
        # Recover ATR from atr_stop_pct (which was stored as 0.5*ATR/price*100)
        raw_atr = atr_stop_pct / 100.0 * price / 0.5
    vol_ok, vol_reason = pc.check_volatility_filter(raw_atr, price, symbol)
    if not vol_ok:
        tui_log(f"VOL FILTER: {symbol} SKIP — {vol_reason}", event_type="SKIP")
        return False

    # Narrow lock scope: only for initial checks
    with state.lock:
        if any(p["symbol"] == symbol for p in state.positions):
            return False

        last_exit = state.last_exit_times.get(symbol, 0)

        if time.time() - last_exit < COOLDOWN_SECONDS:
            remaining_h = (COOLDOWN_SECONDS - (time.time() - last_exit)) / 3600
            tui_log(f"COOLDOWN: {symbol} — {remaining_h:.1f}h remaining before re-entry", event_type="SKIP")
            return False

        signals       = result.get("signals", [])
        is_low_liq    = any("Low Liquidity" in s for s in signals)
        is_htf        = any("HTF Alignment" in s for s in signals)

        # Tiered score gate
        effective_min = (
            pc.SCORE_MIN_LOW_LIQ    if is_low_liq else
            pc.SCORE_MIN_HTF_BYPASS if is_htf     else
            pc.SCORE_MIN_DEFAULT
        )
        if score < effective_min:
            tui_log(f"SKIP: {symbol} score {score} < effective min {effective_min}")
            return False

        # ── Free margin gate (replaces hard position cap) ─────────────────────
        free_margin = get_sim_free_margin(state.balance, state.positions)
        if free_margin < MIN_FREE_MARGIN:
            tui_log(
                f"FREE MARGIN: {symbol} SKIP — only ${free_margin:.2f} free (min ${MIN_FREE_MARGIN:.2f})",
                event_type="SKIP"
            )
            return False

        # Copy state needed for calculations outside the lock
        current_balance = state.balance
        current_stats = state.rolling_stats.copy()

    # ── Upgrade #12: Dynamic risk manager sizing ──────────────────────────
    if _RISK_MGR_OK:
        # Compute ATR-based stop distance for proper unit sizing
        atr_val = raw_atr or (price * 0.02)  # fallback 2% of price
        atr_stop_mult  = float(os.getenv("ATR_STOP_MULT",  "1.5"))
        atr_trail_mult = float(os.getenv("ATR_TRAIL_MULT", "1.0"))
        stop_dist = atr_val * atr_stop_mult

        signal_conf = min(1.0, max(0.0, (score - 100) / 100.0))
        risk_amount, _ = risk_mgr.compute_dynamic_risk(
            account_balance  = current_balance,
            signal_strength  = signal_conf,
            stop_distance    = stop_dist,
            open_positions   = state.positions, # List is stable enough for risk check
        )

        # Portfolio rejection gate
        rejected, rej_reason = risk_mgr.should_reject_trade(
            risk_amount      = risk_amount,
            account_balance  = current_balance,
            open_positions   = state.positions,
        )
        if rejected:
            tui_log(f"RISK MGR: {symbol} SKIP — {rej_reason}", event_type="SKIP")
            return False

        margin_to_use = round(risk_amount, 2)
        tui_log(
            f"RISK MGR [{risk_mgr.RISK_MODEL}]: {symbol} margin={margin_to_use:.4f} ",
            event_type="RISK"
        )

    elif is_low_liq:
        # Low-liquidity fallback (unchanged from original)
        active_leverage  = p_bot.LOW_LIQ_LEVERAGE
        active_trail_pct = p_bot.LOW_LIQ_TRAIL_PCT
        margin_to_use    = p_bot.LOW_LIQ_MARGIN
        tui_log(f"{symbol}: LOW-LIQ MODE — {active_leverage}x lev, "
                f"{active_trail_pct*100:.1f}% trail, ${margin_to_use} margin")
    else:
        # Legacy Kelly fallback
        total_trades  = current_stats["wins"] + current_stats["losses"]
        max_per_trade = current_balance * 0.20  # cap any single trade at 20% of balance

        if total_trades < 10:
            margin_to_use = round(max_per_trade, 2)
        else:
            win_rate = current_stats["wins"] / total_trades
            avg_win  = current_stats["win_pnl"] / current_stats["wins"] if current_stats["wins"] > 0 else 1.0
            avg_loss = abs(current_stats["loss_pnl"] / current_stats["losses"]) if current_stats["losses"] > 0 else 1.0
            
            kelly_margin  = pc.calc_kelly_margin(
                bankroll=current_balance, win_rate=win_rate,
                avg_win=avg_win, avg_loss=avg_loss, fraction=0.5
            )
            margin_to_use = round(min(kelly_margin, max_per_trade), 2)

    if margin_to_use <= 0 or current_balance < margin_to_use:
        tui_log(f"MARGIN FAIL: ${margin_to_use} calculated, but balance ${current_balance:.2f} is insufficient.")
        return False

    # Determine leverage / trail parameters — leverage is now ATR-driven
    vol_spike = result.get("vol_spike", 1.0)
    active_leverage  = pick_sim_leverage(atr_stop_pct, vol_spike, is_low_liq)
    active_trail_pct = p_bot.LOW_LIQ_TRAIL_PCT if is_low_liq else p_bot.TRAIL_PCT
    tui_log(f"LEV SELECT: {symbol} → {active_leverage}x (ATR%={atr_stop_pct}, spike={vol_spike:.1f}x, low_liq={is_low_liq})", event_type="INFO")

    notional = margin_to_use * active_leverage
    size     = notional / price

    # ── Upgrade #1: Realistic slippage simulation ─────────────────────────
    best_bid = result.get("best_bid")
    best_ask = result.get("best_ask")
    fill_price, slippage_amt = pc.calc_slippage(
        price=price,
        direction=direction,
        best_bid=best_bid,
        best_ask=best_ask,
        atr=raw_atr,
    )
    tui_log(
        f"SLIPPAGE: {symbol} mid={price:.6g} fill={fill_price:.6g} ",
        event_type="FILL"
    )
    entry_price = fill_price  # use slippage-adjusted fill

    # Deduct margin AND taker fee
    fee = notional * pc.TAKER_FEE
    
    # ── Upgrade #2: ATR-based stop-loss and trailing stop ─────────────────
    if raw_atr and raw_atr > 0:
        atr_stop_mult  = float(os.getenv("ATR_STOP_MULT",  "1.5"))
        atr_trail_mult = float(os.getenv("ATR_TRAIL_MULT", "1.0"))
        stop_px, trail_dist = pc.calc_atr_stops(
            entry_price = entry_price,
            atr         = raw_atr,
            direction   = direction,
            stop_mult   = atr_stop_mult,
            trail_mult  = atr_trail_mult,
        )
        tui_log(
            f"ATR STOP: {symbol} atr={raw_atr:.6g} stop={stop_px:.6g} ",
            event_type="STOP"
        )
    else:
        # Fallback to percentage-based stops
        if direction == "LONG":
            stop_px = entry_price * (1.0 - active_trail_pct)
        else:
            stop_px = entry_price * (1.0 + active_trail_pct)
        trail_dist = entry_price * active_trail_pct

    # Take-profit unchanged
    if direction == "LONG":
        tp_px      = entry_price * (1.0 + p_bot.TAKE_PROFIT_PCT)
        high_water = entry_price
        low_water  = None
    else:
        tp_px      = entry_price * (1.0 - p_bot.TAKE_PROFIT_PCT)
        high_water = None
        low_water  = entry_price

    new_pos = {
        "symbol":        symbol,
        "side":          side,
        "size":          size,
        "margin":        margin_to_use,
        "leverage":      active_leverage,
        "fee":           fee,
        "entry":         entry_price,
        "mid_price":     price,           # original mid before slippage
        "slippage":      slippage_amt,
        "pnl":           0.0,
        "stop_price":    stop_px,
        "original_stop": stop_px,
        "take_profit":   tp_px,
        "trail_dist":    trail_dist,      # ATR-based trail distance stored for updates
        "high_water":    high_water,
        "low_water":     low_water,
        "timestamp":     datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "entry_time":    datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "entry_score":   score,
        "signals":       signals,
    }

    # Final state update: atomic append and balance deduction
    with state.lock:
        # Re-check position existence inside final lock to avoid races
        if any(p["symbol"] == symbol for p in state.positions):
            return False
        
        state.balance -= (margin_to_use + fee)
        state.positions.append(new_pos)
    
    # Save outside the state.lock to avoid nested lock deadlock
    state.save_account()

    arrow = "▲ LONG" if direction == "LONG" else "▼ SHORT"
    tui_log(f"ENTERED {arrow} {symbol} @ {entry_price:.6g} (Score: {score}) slippage={slippage_amt:.6g}")

    # --- Entry Cinematic ---
    if direction == "LONG":
        play_animation(animations.long)
    else:
        play_animation(animations.short)

    emoji = "🚀" if direction == "LONG" else "📉"
    send_telegram_message(
        f"{emoji} *SIM TRADE OPENED*\n\n"
        f"*Symbol:* {symbol}\n"
        f"*Direction:* {direction}\n"
        f"*Price:* {price}\n"
        f"*Score:* {score}\n"
        f"*Time:* {datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')}"
    )

    p_bot.log_trade({
        "timestamp": new_pos["timestamp"],
        "symbol":    symbol,
        "direction": direction,
        "price":     price,
        "qty":       str(size),
        "score":     score,
        "status":    "simulated_entry",
    })

    _subscribe_symbol(symbol)
    _ensure_ws_started()

    with state.lock:
        if not state.display_thread_running:
            state.display_thread_running = True
            threading.Thread(target=_live_pnl_display, daemon=True).start()

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main Bot Loop
# ─────────────────────────────────────────────────────────────────────────────

# Hoist helper functions out of the loop
def is_fresh(r: dict, now_dt: datetime.datetime) -> bool:
    ts_raw = r.get("scan_timestamp")
    if not ts_raw:
        return True
    try:
        # Handle string or datetime
        ts = datetime.datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else ts_raw
        return (now_dt - ts).total_seconds() < RESULT_STALENESS_SECONDS
    except (ValueError, TypeError):
        return True

# ── Cluster & Entropy Tracking (Idea 2 & 3) ─────────────────────────
_hawkes_long = pc.HawkesTracker(mu=0.1, alpha=0.8, beta=0.1)
_hawkes_short = pc.HawkesTracker(mu=0.1, alpha=0.8, beta=0.1)

def _get_cluster_threshold_penalty(intensity: float) -> int:
    """Returns a score penalty based on Hawkes intensity (λ)."""
    if intensity > HAWKES_INTENSITY_CRITICAL: return 50  # Major cluster
    if intensity > HAWKES_INTENSITY_HIGH:     return 30
    if intensity > HAWKES_INTENSITY_MID:      return 15
    return 0

# REF: Tier 3: Non-Descriptive Variable Naming (r -> scan_res)
def on_scan_result(scan_res: dict, direction: str) -> None:
    result_time_raw = scan_res.get("scan_timestamp")
    if result_time_raw:
        try:
            # Parse ISO string back to datetime for comparison
            if isinstance(result_time_raw, str):
                result_time = datetime.datetime.fromisoformat(result_time_raw)
            else:
                result_time = result_time_raw

            if result_time.tzinfo is None:
                result_time = result_time.replace(tzinfo=datetime.timezone.utc)

            if (datetime.datetime.now(datetime.timezone.utc) - result_time).total_seconds() > RESULT_STALENESS_SECONDS:
                return
        except (ValueError, TypeError):
            pass

    # ── Hawkes Cluster Throttling (Idea 3) ────────────────────
    tracker = _hawkes_long if direction == "LONG" else _hawkes_short
    intensity = tracker.get_intensity() # Check intensity WITHOUT updating it yet
    hawkes_penalty = _get_cluster_threshold_penalty(intensity)

    # Use global entropy_penalty from last scan to block cascades
    with state.lock:
        current_penalty = state.entropy_penalty
    
    effective_fast_track = FAST_TRACK_SCORE + hawkes_penalty + current_penalty
    if scan_res["score"] < effective_fast_track:
        if hawkes_penalty > 0 or current_penalty > 0:
            tui_log(f"FT THROTTLE: {scan_res['inst_id']} score {scan_res['score']} < dynamic FT threshold {effective_fast_track} (λ={intensity:.2f}, H_pen={current_penalty})")
        return

    # Signal passed! Now update the tracker to throttle the NEXT one in this cluster.
    intensity = tracker.update(event_occurred=True)

    # Move position count and balance check inside state.lock for atomicity
    with state.lock:
        current_positions = state.positions
        acc_balance = state.balance
        dynamic_max = p_bot.get_dynamic_max_positions(acc_balance)

        # Gate on free margin instead of position count
        pending_margin = len(state.fast_track_opened) * (p_bot.MARGIN_USDT * 1.05)  # buffer for in-flight verifications
        used_margin = sum(p.get("margin", 0.0) for p in current_positions)
        effective_free = acc_balance - used_margin - pending_margin
        
        if effective_free < MIN_FREE_MARGIN:
            return

        current_syms = {p["symbol"] for p in current_positions}
        if scan_res["inst_id"] in current_syms or scan_res["inst_id"] in state.fast_track_opened:
            return

        if scan_res["score"] < FAST_TRACK_SCORE: # redundant but safe
            return

        last_ft = state.fast_track_cooldowns.get(scan_res["inst_id"], 0)
        if time.time() - last_ft < FAST_TRACK_COOLDOWN_SECONDS:
            return

        state.fast_track_opened.add(scan_res["inst_id"])
        state.fast_track_cooldowns[scan_res["inst_id"]] = time.time()

    tui_log(f"⚡ FAST-TRACK: {scan_res['inst_id']} score {scan_res['score']}! (λ={intensity:.2f})")

    # ── Wait & Verify ────────────────────────────────────
    try:
        verified_result = verify_sim_candidate(scan_res["inst_id"], direction, scan_res["score"])
        if verified_result:
            execute_sim_setup(verified_result, direction)
    except Exception as e:
        import traceback
        tui_log(f"CRITICAL ERROR in on_scan_result for {scan_res['inst_id']}: {e}", event_type="ERROR")
        logger.error(traceback.format_exc())
    finally:
        with state.lock:
            symbol = scan_res["inst_id"]
            if symbol in state.fast_track_opened:
                state.fast_track_opened.remove(symbol)


def sim_bot_loop(args) -> None:
    """The main scan-and-execute loop for the simulation bot."""
    global COOLDOWN_SECONDS

    # Calculate dynamic cooldown based on timeframe and cooldown argument (T3-16)
    tf_sec = p_bot.get_tf_seconds(args.timeframe)
    COOLDOWN_SECONDS = args.cooldown * tf_sec
    logger.info(f"Simulation cooldown set to {COOLDOWN_SECONDS}s ({args.cooldown} candles)")

    cfg = {
        "MIN_VOLUME":     args.min_vol,
        "TIMEFRAME":      args.timeframe,
        "TOP_N":          50,
        "MIN_SCORE":      0,
        "MAX_WORKERS":    args.workers,
        "RATE_LIMIT_RPS": args.rate,
    }

    _ensure_ws_started()
    state.load_account()
    load_sim_cooldowns()

    # --- Cinematic Boot ---
    play_animation(animations.boot)

    with state.lock:
        for p in state.positions:
            _subscribe_symbol(p["symbol"])

    with state.lock:
        if not state.display_thread_running:
            state.display_thread_running = True
            threading.Thread(target=_live_pnl_display, daemon=True).start()

    while True:
        update_pnl_and_stops()

        positions      = get_sim_positions()
        # Recompute dynamic max positions and available slots
        # REF: Tier 3: Non-Descriptive Variable Naming (acc -> account)
        account        = load_paper_account()
        acc_balance    = account.get("balance", 0.0)

        # No slot cap — scan every cycle, gate on margin at execution time
        tui_log(f"Scanning LIVE market ({args.timeframe})...")
        state.display_paused_event.set()
        t0 = time.time()
        long_r, short_r = p_bot.run_scanner_both(cfg, args, on_result=on_scan_result)
        elapsed = time.time() - t0
        state.display_paused_event.clear()
        tui_log(f"Scan complete in {elapsed:.1f}s — L: {len(long_r)}  S: {len(short_r)}")

        # ── Cross-Asset Entropy Deflator (Idea 2) ─────────────────────
        all_tickers = pc.get_tickers(rps=args.rate)
        total_universe = len([t for t in all_tickers if float(t.get("turnoverRv", 0)) >= args.min_vol])

        n_hits = len(long_r) + len(short_r)
        new_entropy_penalty = 0
        if total_universe > 0 and n_hits > 0:
            # Saturation: percentage of universe firing
            sat_ratio = n_hits / total_universe
            # Capped and less aggressive entropy penalties
            sat_penalty = min(ENTROPY_SAT_CAP, int(sat_ratio * ENTROPY_SAT_WEIGHT)) 

            # One-sidedness: how imbalanced are the signals?
            imbalance = abs(len(long_r) - len(short_r)) / n_hits
            side_penalty = int(ENTROPY_IMB_WEIGHT * imbalance)

            new_entropy_penalty = min(ENTROPY_MAX_PENALTY, sat_penalty + side_penalty)

        with state.lock:
            state.entropy_penalty = new_entropy_penalty

        if new_entropy_penalty > ENTROPY_ALERT_LEVEL:
            tui_log(f"ENTROPY DEFLATOR: Raising min_score by +{new_entropy_penalty} (Saturation: {n_hits}/{total_universe}, Imbalance: {imbalance:.2f})")

        # Calculate dynamic threshold for this scan cycle
        eff_min_score = args.min_score + new_entropy_penalty
        if not args.no_dynamic:
            all_scores = [r["score"] for r in (long_r + short_r)]
            dynamic_min = pc.calc_dynamic_threshold(all_scores, args.min_score)
            eff_min_score = max(eff_min_score, dynamic_min)

        if eff_min_score > args.min_score:
            tui_log(f"ADAPTIVE FILTER: Effective min_score = {eff_min_score} (Penalty: +{new_entropy_penalty})")

        now_dt = datetime.datetime.now(datetime.timezone.utc)

        fresh_long  = [r for r in long_r  if is_fresh(r, now_dt)]
        fresh_short = [r for r in short_r if is_fresh(r, now_dt)]

        in_pos_updated   = {p["symbol"] for p in get_sim_positions()}

        candidates = p_bot.pick_candidates(
            fresh_long, fresh_short,
            min_score=eff_min_score,
            min_score_gap=args.min_score_gap,
            direction_filter=args.direction,
            in_position=in_pos_updated,
            available_slots=9999,
        )

        if candidates:
            tui_log(f"Picked {len(candidates)} candidate(s).")
            for res, direction in candidates:
                # Account for positions already open PLUS those currently being verified by fast-track
                with state.lock:
                    eff_free = get_sim_free_margin(state.balance, state.positions)
                    eff_free -= len(state.fast_track_opened) * (p_bot.MARGIN_USDT * 1.05)
                    if eff_free < MIN_FREE_MARGIN:
                        continue

                # ── Wait & Verify ────────────────────────────────────
                try:
                    verified_result = verify_sim_candidate(res["inst_id"], direction, res["score"])
                    if verified_result:
                        execute_sim_setup(verified_result, direction)
                except Exception as e:
                    import traceback
                    tui_log(f"CRITICAL ERROR in candidate loop for {res['inst_id']}: {e}", event_type="ERROR")
                    logger.error(traceback.format_exc())
        else:
            tui_log("No qualifying setups found.")

        sleep_interval = args.interval
        state.slot_available_event.wait(timeout=sleep_interval)
        state.slot_available_event.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Parses arguments and starts the simulation bot."""
    parser = argparse.ArgumentParser(description="Phemex Sim Bot (Paper Trading)")
    parser.add_argument("--interval",       type=int,   default=300)
    parser.add_argument("--min-score",      type=int,   default=125)
    parser.add_argument("--min-score-gap",  type=int,   default=30)
    parser.add_argument("--direction",      default="BOTH", choices=["LONG", "SHORT", "BOTH"])
    parser.add_argument("--timeframe",      default="4H")
    parser.add_argument("--cooldown",       type=int,   default=4, help="Cooldown in candles after exit")
    parser.add_argument("--min-vol",        type=int,   default=1_000_000)
    parser.add_argument("--workers",        type=int,   default=30)
    parser.add_argument("--rate",           type=float, default=20.0)
    parser.add_argument("--no-ai",          action="store_true")
    parser.add_argument("--no-entity",      action="store_true")
    parser.add_argument("--no-dynamic",     action="store_true")
    args = parser.parse_args()

    print(Fore.GREEN + Style.BRIGHT + "  🚀 Phemex SIMULATION Bot Starting (Paper Trading)")
    print(f"  Market   : LIVE (api.phemex.com)")
    print(f"  Account  : LOCAL (paper_account.json)")
    print(f"  Balance  : {INITIAL_BALANCE} USDT")
    print(f"  Interval : {args.interval}s")
    print(f"  Score    : {args.min_score} (gap: {args.min_score_gap})  Direction: {args.direction}\n")

    # ── Upgrade #7: Initialise daily drawdown guard ───────────────────────────
    if _DD_OK:
        drawdown_guard.set_start_balance(INITIAL_BALANCE)
        logger.info(f"drawdown_guard: active — max daily drawdown {drawdown_guard.MAX_DAILY_DRAWDOWN:.1%}")

    # ── Upgrade #13: Start Telegram control interface ─────────────────────────
    if _TG_OK:
        def _session_pnl_fn():
            with state.lock:
                return {
                    "wins":      state.rolling_stats["wins"],
                    "losses":    state.rolling_stats["losses"],
                    "total_pnl": state.rolling_stats["win_pnl"] + state.rolling_stats["loss_pnl"],
                }
        telegram.start(
            get_balance_fn     = get_sim_balance,
            get_positions_fn   = get_sim_positions,
            get_session_pnl_fn = _session_pnl_fn,
        )
        logger.info("telegram_controller: started")

    try:
        sim_bot_loop(args)
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n  Bot stopped by user. Shutting down...")
        # Signal the display thread to stop and wait for it
        with state.lock:
            if state.display_thread_running:
                state.display_paused_event.set() # Signal to stop drawing
                state.display_thread_running = False

        # Ensure WebSocket client is closed if it was started
        if state.ws_app:
            try:
                state.ws_app.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")

        print(Fore.YELLOW + "  Shutdown complete.")


if __name__ == "__main__":
    main()
