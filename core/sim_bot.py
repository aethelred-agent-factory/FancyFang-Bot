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
Phemex Simulation (Paper Trading) Bot
======================================
Runs on LIVE production market data but simulates all trades locally.
Maintains a local 'paper_account.json' to track balance and positions.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import argparse
import datetime
import json
import logging
import os
import queue
import re
import sys
import sys
import threading
import time
import signal
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    pass

import matplotlib
matplotlib.use('Agg')
import blessed
import requests
import websocket
from colorama import Fore, Style, init

import core.phemex_common as pc
import core.phemex_long as scanner_long
import core.phemex_short as scanner_short
import core.ui as ui
from core.ui_textual import FancyBotApp
import core.web_bridge as web_bridge
import modules.animations as animations
import modules.hardware_bridge as hw
from modules.banner import BANNER
from modules.storage_manager import StorageManager

# ── Global Control ───────────────────────────────────────────────────
_running = True
_shutdown_requested = False

def handle_exit(signum, frame):
    """Force an immediate exit on signal, ensuring all loops are terminated."""
    global _running, _shutdown_requested
    if not _shutdown_requested:
        _shutdown_requested = True
        msg = f"Signal {signum} received. Forcing immediate shutdown..."
        try:
            logging.getLogger("sim_bot").info(msg)
        except Exception:
            print(msg)
        _running = False
        sys.exit(0)

signal.signal(signal.SIGINT,  handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# Safely import core.p_bot as p_bot
try:
    import core.p_bot as p_bot
except ImportError:
    msg = "CRITICAL: 'p_bot.py' not found. This module is required for risk parameters."
    print(Fore.RED + msg)
    raise pc.InitializationError(msg)

# ── Upgrade modules (graceful degradation if missing) ─────────────────────────
try:
    import modules.signal_analytics as analytics
    _ANALYTICS_OK = True
except ImportError:
    _ANALYTICS_OK = False

try:
    import modules.risk_manager as risk_mgr
    _RISK_MGR_OK = True
except ImportError:
    _RISK_MGR_OK = False

try:
    import modules.drawdown_guard as drawdown_guard
    _DD_OK = True
except ImportError:
    _DD_OK = False

try:
    import modules.telegram_controller as telegram
    _TG_OK = True
except ImportError:
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

# ─────────────────────────────────────────────────────────────────────────────
# Configuration & Constants
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR          = Path(__file__).parent

# Initialize colorama for TUI colors
init(autoreset=True)

PAPER_ACCOUNT_FILE  = SCRIPT_DIR.parent / "data" / "state" / "paper_account.json"
SIM_COOLDOWN_FILE   = SCRIPT_DIR.parent / "data" / "state" / "sim_cooldowns.json"
INITIAL_BALANCE     = float(os.getenv("INITIAL_BALANCE", "100.0"))
TAKER_FEE_RATE      = pc.TAKER_FEE  # Use common constant (0.06%)

def get_sim_free_margin(balance: float, positions: List[Dict[str, Any]]) -> float:
    """Returns balance not committed to open positions."""
    used = sum(p.get("margin", 0.0) for p in positions)
    return balance - used

MIN_FREE_MARGIN = float(os.getenv("BOT_MIN_FREE_MARGIN", "5.0"))
MAX_MARGIN_PER_SYMBOL = float(os.getenv("MAX_MARGIN_PER_SYMBOL", "100.0"))

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
# ── Dynamic Cooldown Parameters (Project Phoenix) ───────────────────────────
# After a trade is closed, the cooldown before re-entry is dynamically calculated.
BASE_COOLDOWN_WIN_S           = int(os.getenv("BASE_COOLDOWN_WIN_S", "300"))      # 5 mins on win
BASE_COOLDOWN_LOSS_S          = int(os.getenv("BASE_COOLDOWN_LOSS_S", "1800"))     # 30 mins base on loss
PNL_COOLDOWN_MULTIPLIER       = int(os.getenv("PNL_COOLDOWN_MULTIPLIER", "72"))   # 72s per dollar lost (e.g., -$25 loss adds 30 mins)
ENTROPY_COOLDOWN_REDUCTION_F  = int(os.getenv("ENTROPY_COOLDOWN_REDUCTION_F", "120")) # 120s reduction per entropy point
MAX_COOLDOWN_S                = int(os.getenv("MAX_COOLDOWN_S", "14400"))    # 4 hour max cooldown

# Backwards-compatible static cooldown
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
HAWKES_INTENSITY_CRITICAL = float(os.getenv("HAWKES_CRITICAL", "4.0")) # -> +0.50 penalty
HAWKES_INTENSITY_HIGH     = float(os.getenv("HAWKES_HIGH", "3.0"))     # -> +0.30 penalty
HAWKES_INTENSITY_MID      = float(os.getenv("HAWKES_MID", "2.0"))      # -> +0.15 penalty

# Entropy Deflator Parameters (scaled for predictive score)
ENTROPY_MAX_PENALTY   = float(os.getenv("ENTROPY_MAX_PENALTY", "0.30"))
ENTROPY_SAT_WEIGHT    = float(os.getenv("ENTROPY_SAT_WEIGHT", "0.20"))
ENTROPY_SAT_CAP       = float(os.getenv("ENTROPY_SAT_CAP", "0.20"))
ENTROPY_IMB_WEIGHT    = float(os.getenv("ENTROPY_IMB_WEIGHT", "0.10"))
ENTROPY_ALERT_LEVEL   = float(os.getenv("ENTROPY_ALERT_LEVEL", "0.20"))

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
    last_exit_info: Dict[str, Tuple[float, float]] = field(default_factory=dict) # symbol -> (timestamp, pnl)
    fast_track_cooldowns: Dict[str, float] = field(default_factory=dict)
    fast_track_opened: set[str] = field(default_factory=set)
    
    # Analytics & Stats
    rolling_stats: Dict[str, Any] = field(default_factory=lambda: {"wins": 0, "losses": 0, "win_pnl": 0.0, "loss_pnl": 0.0})
    equity_history: List[float] = field(default_factory=list)
    pnl_histories: Dict[str, List[float]] = field(default_factory=dict)
    entropy_penalty: float = 0.0
    
    # System Control
    is_running: bool = True
    no_tui: bool = False
    display_thread_running: bool = False
    ws_app: Optional[websocket.WebSocketApp] = None
    ws_thread: Optional[threading.Thread] = None
    
    # Events
    slot_available_event: threading.Event = field(default_factory=threading.Event)
    display_paused_event: threading.Event = field(default_factory=threading.Event)
    force_scan_event: threading.Event = field(default_factory=threading.Event)
    
    # Queues
    animation_queue: queue.Queue = field(default_factory=queue.Queue)
    
    # Locks
    lock: threading.RLock = field(default_factory=threading.RLock)
    file_io_lock: threading.Lock = field(default_factory=threading.Lock)
    storage: StorageManager = field(default_factory=lambda: StorageManager(SCRIPT_DIR.parent / "data" / "state" / "fancybot.db"))

    def load_account(self):
        """Loads paper account from storage into memory."""
        # REF: [Tier 1] Lock Hierarchy (file_io_lock -> lock)
        with self.file_io_lock:
            account_data = self.storage.load_account(initial_balance=INITIAL_BALANCE)
            with self.lock:
                self.balance = account_data["balance"]
                self.positions = account_data["positions"]

    def save_account(self):
        """Flushes in-memory account state to storage."""
        # REF: [Tier 1] Lock Hierarchy (file_io_lock -> lock)
        with self.file_io_lock:
            with self.lock:
                balance = self.balance
                positions = self.positions[:]
            try:
                self.storage.save_account_state(balance, positions)
            except Exception as error:
                logging.getLogger("sim_bot").error(f"Failed to save paper account: {error}")

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

# Initialize unified storage for upgrade modules
if _ANALYTICS_OK:
    analytics.init_storage(state.storage)
if _DD_OK:
    drawdown_guard.init_storage(state.storage)
if _CM_OK:
    corr_mgr.init(state.storage)
if _EF_OK:
    event_filter.init(state.storage)

# Unicode Block Elements U+2581–U+2588 (8 chars; index math in sparkline() depends on count=8)
_SPARK_CHARS = "▁▂▃▄▅▆▇█"

# TUI log buffer
_bot_logs: deque[str] = deque(maxlen=100)


def update_pnl_history(symbol: str, current_pnl: float):
    """Adds a new PnL data point to the history for the given symbol."""
    with state.lock:
        if symbol not in state.pnl_histories:
            state.pnl_histories[symbol] = []
        state.pnl_histories[symbol].append(current_pnl)
        # Keep last 200 data points
        state.pnl_histories[symbol] = state.pnl_histories[symbol][-200:]

# ── Logging Setup ─────────────────────────────────────────────────────
# Use the shared colored logging setup from core.phemex_common with buffer capture
logger = pc.setup_colored_logging(
    "sim_bot",
    level=logging.INFO,
    log_file=Path(SCRIPT_DIR).parent / "data" / "logs" / "sim_bot.log",
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

def _get_tui_logs() -> str:
    """Returns the last 15 lines of system logs as a single string."""
    return "\n".join(list(_bot_logs)[-15:])

def _manual_tg_scan(args) -> str:
    """Triggers a manual dual-direction scan and returns a formatted report for Telegram."""
    # Use current bot config
    cfg = {
        "MIN_VOLUME":     args.min_vol,
        "TIMEFRAME":      args.timeframe,
        "TOP_N":          5,
        "MIN_SCORE":      0,
        "MAX_WORKERS":    args.workers,
        "RATE_LIMIT_RPS": args.rate,
        "CANDLES":        500,
    }
    # Create a dummy args for scanner
    class DummyArgs:
        no_ai = True
        no_entity = True

    try:
        long_r, short_r = p_bot.run_scanner_both(cfg, DummyArgs())

        # Format a brief report
        lines = [f"🔍 *SIM Manual Scan ({args.timeframe})*"]

        tagged_long  = [dict(r, _dir="LONG")  for r in long_r]
        tagged_short = [dict(r, _dir="SHORT") for r in short_r]
        combined = sorted(tagged_long + tagged_short, key=lambda x: x["score"], reverse=True)

        top = combined[:8]
        if not top:
            lines.append("No instruments found matching criteria.")
        else:
            for r in top:
                direction = r.get("_dir", "?")
                arrow = "▲" if direction == "LONG" else "▼"
                lines.append(f"{arrow} `{r['inst_id']}` | Score: `{r['score']}` | Price: `{r['price']:.5g}`")

        return "\n".join(lines)
    except Exception as e:
        return f"Scan failed: {e}"


def _get_session_chart() -> Optional[str]:
    """Generates a PnL chart using matplotlib and returns the file path."""
    try:
        import matplotlib.pyplot as plt
        import os

        # For sim_bot, we might need to track equity history if not already tracked
        # but for now we'll use a placeholder or pull from storage
        with state.lock:
            # Simple placeholder: just use current balance if history is empty
            data = [state.balance]

        plt.figure(figsize=(10, 5))
        plt.plot(data, marker='o', linestyle='-', color='g')
        plt.title(f"Sim Session Equity ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})")
        plt.xlabel("Points")
        plt.ylabel("Equity (USDT)")
        plt.grid(True)

        logs_dir = Path(SCRIPT_DIR).parent / "data" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        chart_path = logs_dir / f"sim_session_chart_{int(time.time())}.png"
        plt.savefig(chart_path)
        plt.close()

        return str(chart_path)
    except Exception as e:
        logger.error(f"Failed to generate sim chart: {e}")
        return None


def _run_manual_backtest(text: str, args) -> str:
    """Parses backtest command and runs a mini backtest."""
    import research.backtest as bt

    parts = text.split()
    # /backtest [symbol] [timeframe] [candles]
    symbol = parts[1].upper() if len(parts) > 1 else "BTCUSDT"
    tf = parts[2] if len(parts) > 2 else args.timeframe
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
            min_score=args.min_score, trail_pct=args.trail_pct, leverage=args.leverage,
            margin=args.margin, max_margin=150.0,
            direction=args.direction
        )

        if not trades:
            return f"No trades triggered for {symbol} ({tf}, {candles} candles)."

        # Format brief report
        win_trades = [t for t in trades if t.pnl_usdt > 0]
        total_pnl = sum(t.pnl_usdt for t in trades)

        report = [
            f"🧪 *SIM Backtest Results: {symbol}*",
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
        logger.error(f"Sim backtest callback error: {e}")
        return f"Error: {e}"



def play_animation(anim_fn):
    """Queues a cinematic animation to be played safely."""
    state.animation_queue.put(anim_fn)


def _process_animations():
    """Processes any queued animations. Should be called from a safe thread context (main loop)."""
    while not state.animation_queue.empty():
        anim_fn = state.animation_queue.get()
        state.display_paused_event.set()
        time.sleep(0.5) # Let TUI finish its last frame
        animations.clear()
        try:
            anim_fn()
        except Exception as e:
            logger.error(f"Animation failed: {e}")
        finally:
            animations.clear()
            state.display_paused_event.clear()
            state.animation_queue.task_done()


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
    with state.lock:
        positions = state.positions[:]
        balance = state.balance

    if not positions:
        print(Fore.YELLOW + "  No positions to close.")
        return

    print(Fore.CYAN + f"  Closing {len(positions)} positions...")

    # --- Kill Cinematic ---
    play_animation(animations.kill)
    hw.bridge.signal('EXIT')

    new_balance = balance
    for pos in positions:
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
        new_balance += (pos.get("margin", 0.0) + pnl)

        with state.lock:
            state.last_exit_info[symbol] = (time.time(), pnl)

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

    with state.lock:
        state.balance = new_balance
        state.positions = []
    state.save_account()
    save_sim_cooldowns()
    state.slot_available_event.set()
    print(Fore.GREEN + Style.BRIGHT + "  All positions closed successfully.")


def save_sim_cooldowns() -> None:
    """Persists active re-entry and fast-track cooldowns to disk, pruning expired entries."""
    with state.lock:
        # Prune expired entries based on the MAX possible cooldown
        active_exit = {s: info for s, info in state.last_exit_info.items() if time.time() - info[0] < MAX_COOLDOWN_S}
        active_ft   = {s: ts for s, ts in state.fast_track_cooldowns.items() if time.time() - ts < FAST_TRACK_COOLDOWN_SECONDS}

    data = {
        "last_exit_info": active_exit,
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
        
        # New format check
        if isinstance(data, dict) and "last_exit_info" in data:
            exit_data = data["last_exit_info"]
        # Legacy format check
        elif isinstance(data, dict) and "last_exit" in data:
            # Convert legacy format {symbol: timestamp} to new format {symbol: [timestamp, 0.0]}
            exit_data = {s: [ts, 0.0] for s, ts in data["last_exit"].items()}
        else: # very old legacy format
            exit_data = {s: [ts, 0.0] for s, ts in data.items()}

        ft_data   = data.get("fast_track", {})

        with state.lock:
            state.last_exit_info = {
                s: (float(info[0]), float(info[1])) for s, info in exit_data.items()
                if time.time() - float(info[0]) < MAX_COOLDOWN_S
            }
            state.fast_track_cooldowns = {
                s: float(ts) for s, ts in ft_data.items()
                if time.time() - float(ts) < FAST_TRACK_COOLDOWN_SECONDS
            }
        logger.info(f"Loaded {len(state.last_exit_info)} exit and {len(state.fast_track_cooldowns)} fast-track cooldowns.")
    except (json.JSONDecodeError, ValueError, AttributeError, IndexError):
        logger.error("Failed to load or parse simulation cooldowns — JSON may be invalid or malformed.")


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
    import traceback
    if state.ws_thread is None or not state.ws_thread.is_alive():
        # REF: [Tier 1] Critical Thread Error Handling
        def _ws_wrapper():
            try:
                _ws_run_loop()
            except Exception as error:
                logger.error(f"WS run loop crashed: {error}\n{traceback.format_exc()}")

        state.ws_thread = threading.Thread(target=_ws_wrapper, daemon=True)
        state.ws_thread.start()


def _subscribe_symbol(symbol: str) -> None:
    """Subscribes the WebSocket to a new symbol after a short delay."""
    def _do_sub() -> None:
        import traceback
        try:
            time.sleep(1.5)
            if state.ws_app and state.ws_app.sock and state.ws_app.sock.connected:
                with state.lock:
                    symbols = [p["symbol"] for p in state.positions]
                state.ws_app.send(json.dumps({"id": 1, "method": "market24h_p.subscribe", "params": symbols}))
        except Exception as e:
            logger.error(f"Subscription thread failed for {symbol}: {e}\n{traceback.format_exc()}")

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
                "stop_hit": stop_hit,
                "raw_signals": pos.get("raw_signals", {}),
            }

            # Update in-memory state
            state.balance += (pos.get("margin", 0.0) + pnl)
            state.positions.pop(pos_idx)
            state.last_exit_info[symbol] = (time.time(), pnl)
        
    # Process I/O outside the lock
    state.save_account()

    if exit_to_process:
        save_sim_cooldowns()
        state.slot_available_event.set()

        tui_log(f"{exit_to_process['exit_reason'].upper()} HIT: {symbol} {exit_to_process['side']} closed at {exit_to_process['exit_price']}", event_type="EXIT")

        pnl_emoji = "✅" if exit_to_process["pnl"] > 0 else "❌"

        # --- Exit Cinematic ---
        if exit_to_process["pnl"] > 0:
            hw.bridge.signal('TP')
        else:
            hw.bridge.signal('SL')

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
            "stop" if exit_to_process["stop_hit"] else "tp",
            raw_signals=exit_to_process.get("raw_signals", {})
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
    raw_signals: Optional[Dict[str, Any]] = None,
) -> None:
    """Appends a closed-trade record to storage."""
    pnl = (exit_price - entry) * size if direction == "Buy" else (entry - exit_price) * size

    hold_time_seconds = 0
    if entry_time:
        try:
            entry_time_dt = datetime.datetime.fromisoformat(entry_time)
            if entry_time_dt.tzinfo is None:
                entry_time_dt = entry_time_dt.replace(tzinfo=datetime.timezone.utc)
            hold_time_seconds = int((datetime.datetime.now(datetime.timezone.utc) - entry_time_dt).total_seconds())
        except (ValueError, TypeError):
            logger.error("Invalid entry_time format — using zero hold time.")

    # Standardize on timezone-aware UTC for JSON storage
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "symbol":      symbol,
        "direction":   "LONG" if direction == "Buy" else "SHORT",
        "entry":       entry,
        "exit":        exit_price,
        "pnl":         round(pnl, 4),
        "hold_time_s": hold_time_seconds,
        "score":       entry_score,
        "reason":      reason,
        "timestamp":   now_utc.isoformat(),
        "signals":     signals or [],
        "slippage":    round(slippage, 8),
        "raw_signals": raw_signals or {},
    }

    # REF: [Tier 1] Lock Hierarchy (file_io_lock only for storage)
    with state.file_io_lock:
        try:
            state.storage.append_trade(record)
        except Exception as e:
            logger.error(f"Failed to append trade to history: {e}")

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
                timestamp    = entry_time,
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
            current_balance = state.balance
        drawdown_guard.record_pnl(pnl, current_balance=current_balance)

    # ── Upgrade #12: Notify risk manager ─────────────────────────────────────
    if _RISK_MGR_OK:
        risk_mgr.record_trade_result(pnl)

    minutes, seconds = divmod(hold_time_seconds, 60)
    tui_log(
        f"CLOSED {symbol} | Entry: {entry}  Exit: {exit_price} | "
        f"PnL: {pnl:+.4f} USDT | Held: {minutes}m {seconds}s | Score: {entry_score}",
        event_type="HISTORY"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TUI — Drawing Helpers  (v2)
# ─────────────────────────────────────────────────────────────────────────────

def _live_pnl_display() -> None:
    """Full-screen TUI dashboard using blessed."""
    term = blessed.Terminal()

    def draw_panel(x: int, y: int, panel_text: str):
        """Helper to print multi-line panels at specific coordinates."""
        for i, line in enumerate(panel_text.split("\n")):
            print(term.move_xy(x, y + i) + line)

    with term.fullscreen(), term.hidden_cursor():
        while state.display_thread_running:
            if state.display_paused_event.is_set():
                time.sleep(0.5)
                continue

            with state.lock:
                balance = state.balance
                positions = state.positions[:]
                live_prices = state.live_prices.copy()

            with state.file_io_lock:
                history = state.storage.get_trade_history(limit=200)

            print(term.clear)
            
            # --- Header ---
            curr_time = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
            pulse = "●" if int(time.time()) % 2 == 0 else " "
            banner_lines = BANNER.split("\n")
            for i, line in enumerate(banner_lines):
                print(term.move_xy(2, i+1) + ui.gradient_text(line, (0, 255, 255), (255, 0, 255)))
            
            header_y = len(banner_lines) + 1
            print(term.move_xy(2, header_y) + ui.hr_double(Fore.MAGENTA))
            print(term.move_xy(2, header_y + 1) + term.bold_white(f" {Fore.MAGENTA}{pulse}{Style.RESET_ALL} SIMULATION DASHBOARD | {curr_time} UTC"))

            # --- Layout ---
            max_w = term.width - 4
            left_w = int(max_w * 0.65)
            right_w = max_w - left_w - 2
            start_y = header_y + 3

            # --- Left Column ---
            y = start_y

            # 1. Account Summary
            current_upnl = 0.0
            locked_margin = 0.0
            for p in positions:
                locked_margin += p.get("margin", 0.0)
                now = live_prices.get(p["symbol"])
                if now:
                    upnl = (now - p['entry']) * p['size'] if p['side'] == "Buy" else (p['entry'] - now) * p['size']
                    current_upnl += upnl
                    update_pnl_history(p["symbol"], upnl)
            
            equity = balance + locked_margin + current_upnl
            summary_lines = [
                ui.cyber_telemetry("Wallet", balance, INITIAL_BALANCE * 2, "$"),
                ui.cyber_telemetry("uPnL", current_upnl, INITIAL_BALANCE * 0.2, "$"),
                f" Equity: {Style.BRIGHT}${equity:.2f}{Style.RESET_ALL} | Margin: {Fore.YELLOW}${locked_margin:.1f}{Style.RESET_ALL}",
                f" Entropy Penalty: {Fore.MAGENTA}{state.entropy_penalty:.2f}{Style.RESET_ALL}"
            ]
            
            draw_panel(2, y, ui.glow_panel("SYSTEM CORE", summary_lines, color_rgb=(0, 255, 255), width=left_w))
            y += len(summary_lines) + 3

            # 2. Positions
            if not positions:
                draw_panel(2, y, ui.modern_panel("ACTIVE POSITIONS", [Fore.WHITE + " (Monitoring for signals...)"], width=left_w))
                y += 4
            else:
                # Calculate how many positions we can show
                remaining_h = (term.height - 2) - y - 10 # 10 lines for logs and footer
                pos_h = 9 # Height of a position card + gap
                max_show = max(1, remaining_h // pos_h)
                
                for pos in positions[:max_show]:
                    sym = pos["symbol"]
                    now = live_prices.get(sym)
                    if not now:
                        draw_panel(2, y, ui.modern_panel(sym, ["Waiting for price..."], width=left_w))
                        y += 4; continue

                    upnl = (now - pos['entry']) * pos['size'] if pos['side'] == "Buy" else (pos['entry'] - now) * pos['size']
                    hist = state.pnl_histories.get(sym, [0.0])
                    chart = ui.render_pnl_chart(hist, width=left_w-24, height=2)

                    # Calculate stop distance percentage
                    stop_px = pos.get("stop_price", pos['entry'])
                    total_range = abs(pos['entry'] - stop_px) or 1e-10
                    dist_to_stop = abs(now - stop_px)
                    stop_pct = (dist_to_stop / total_range) * 100
                    stop_bar = ui.braille_progress_bar(stop_pct, width=15)

                    # Price Line System
                    price_line = ui.render_price_line(
                        current_price=now,
                        stop_price=stop_px,
                        take_profit=pos.get("take_profit", pos['entry']*1.1),
                        pnl_val=upnl,
                        width=left_w-4
                    )

                    # Extract macro metrics from raw_signals if present
                    raw = pos.get("raw_signals", {})
                    rsi_val = raw.get("rsi")
                    adx_val = raw.get("adx")
                    poc_px  = raw.get("poc_price")
                    spread  = pos.get("spread")
                    
                    rsi_str = f"RSI: {rsi_val:.1f}" if rsi_val is not None else "RSI: N/A"
                    adx_str = f"ADX: {adx_val:.1f}" if adx_val is not None else "ADX: N/A"
                    spr_str = f"Spr: {spread:.3f}%" if spread is not None else "Spr: N/A"
                    
                    header = f"{'▲' if pos['side']=='Buy' else '▼'} {sym} {ui.pnl_color(upnl)}{upnl:+.4f}{Style.RESET_ALL}"
                    
                    info = [
                        f" Entry: {pos['entry']:.5g} | Now: {now:.5g} | Lev: {pos.get('leverage','?')}x",
                        f" {rsi_str} | {adx_str} | Stop Guard: [{stop_bar}]",
                        f" {chart[0]}", f" {chart[1]}",
                        f" {price_line}"
                    ]
                    
                    if poc_px:
                        dist_poc = (now - poc_px) / poc_px * 100.0
                        info.insert(2, f" POC: {poc_px:.5g} (Dist: {dist_poc:+.2f}%)")

                    draw_panel(2, y, ui.glow_panel(header, info, width=left_w, color_rgb=(255, 0, 255)))
                    y += len(info) + 2

                if len(positions) > max_show:
                    print(term.move_xy(2, y) + term.italic_white(f"  ... and {len(positions) - max_show} more positions hidden"))
                    y += 2

            # 3. Logs
            logs_y = term.height - 8
            log_lines = list(_bot_logs)[-5:]
            while len(log_lines) < 5: log_lines.insert(0, "")
            draw_panel(2, logs_y, ui.modern_panel("LOGS", log_lines, width=left_w, color=Fore.WHITE))

            # --- Right Column: History ---
            hist_lines = []
            if history:
                wins = len([t for t in history if float(t["pnl"]) > 0])
                wr = (wins / len(history) * 100) if history else 0
                tot = sum(float(t["pnl"]) for t in history)
                hist_lines.append(f"Trades: {len(history)} | WR: {wr:.0f}%")
                hist_lines.append(f"PnL: {ui.pnl_color(tot)}{tot:+.2f}{Style.RESET_ALL}")
                hist_lines.append(ui.hr_dash(width=right_w-4))
                # Limit history lines to terminal height
                avail_h = term.height - start_y - 10
                for t in reversed(history[-avail_h:]):
                    p = float(t['pnl'])
                    ts = t['timestamp'][11:16]
                    s = t['symbol'].replace('USDT','')
                    hist_lines.append(f"{ts} {s:<6} {ui.pnl_color(p)}{p:+.2f}{Style.RESET_ALL}")

            draw_panel(left_w + 4, start_y, ui.modern_panel("HISTORY", hist_lines, width=right_w))

            # --- Footer ---
            footer = f" [O] Scan  [S] Close All  [Q] Quit | Status: {'RUNNING' if _running else 'STOPPED'}"
            print(term.move_xy(2, term.height-1) + ui.hr_thin(Fore.MAGENTA))
            print(term.move_xy(2, term.height) + term.bold_white(footer))

            key = term.inkey(timeout=0.8)
            if key.lower() == 'o': state.force_scan_event.set()
            elif key.lower() == 's': _close_all_positions()
            elif key.lower() == 'q': break

            # --- Right Column: History ---
            hist_lines = []
            if history:
                wins = len([t for t in history if float(t["pnl"]) > 0])
                wr = (wins / len(history) * 100)
                tot = sum(float(t["pnl"]) for t in history)
                hist_lines.append(f"Trades: {len(history)} | WR: {wr:.0f}%")
                hist_lines.append(f"PnL: {ui.pnl_color(tot)}{tot:+.2f}{Style.RESET_ALL}")
                hist_lines.append(ui.hr_dash())
                for t in reversed(history[-18:]):
                    p = float(t['pnl'])
                    ts = t['timestamp'][11:16]
                    s = t['symbol'].replace('USDT','')
                    hist_lines.append(f"{ts} {s:<6} {ui.pnl_color(p)}{p:+.2f}{Style.RESET_ALL}")

            print(term.move_xy(left_w + 4, start_y) + ui.modern_panel("HISTORY", hist_lines, width=right_w))

            # --- Footer ---
            footer = f" [O] Scan  [S] Close All  [Q] Quit | Status: {'RUNNING' if _running else 'STOPPED'}"
            print(term.move_xy(2, term.height-1) + ui.hr_thin(Fore.MAGENTA))
            print(term.move_xy(2, term.height) + term.bold_white(footer))

            key = term.inkey(timeout=0.8)
            if key.lower() == 'o': state.force_scan_event.set()
            elif key.lower() == 's': _close_all_positions()
            elif key.lower() == 'q': break

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
            "CANDLES": 500
        }

        res = scanner.analyse(ticker, cfg, enable_ai=False, enable_entity=False)
        if res and res["score"] >= EXIT_SIGNAL_SCORE_THRESHOLD:
            return True, res["score"]

    except Exception as e:
        logger.debug(f"Error in check_opposite_signal for {symbol}: {e}")

    return False, 0


def update_pnl_and_stops(args) -> None:
    """
    Polls live prices for all open positions, updates PnL, and evaluates
    trailing-stop and take-profit levels.
    """
    if not state.no_tui:
        _process_animations()

    # Update equity history for charting even in --no-tui mode
    with state.lock:
        balance = state.balance
        locked_margin = sum(p.get("margin", 0.0) for p in state.positions)
        current_upnl = sum(p.get("pnl", 0.0) for p in state.positions)
        equity = balance + locked_margin + current_upnl
        state.equity_history.append(equity)
        if len(state.equity_history) > 100:
            state.equity_history.pop(0)

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
    state_changed = False

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
                old_stop = stop_price
                # Use ATR-based trail via pc.update_atr_trail
                new_stop, new_hw, new_lw = pc.update_atr_trail(
                    current_price  = current_price,
                    stop_price     = stop_price,
                    high_water     = pos.get("high_water") or current_price,
                    low_water      = pos.get("low_water")  or current_price,
                    trail_distance = trail_dist,
                    direction      = direction_str,
                )
                if new_stop != old_stop:
                    state_changed = True
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
                        state_changed = True
                else:
                    if current_price < pos.get("low_water", 999_999_999.0):
                        pos["low_water"]  = current_price
                        pos["stop_price"] = current_price * (1.0 + p_bot.TRAIL_PCT)
                        stop_price = pos["stop_price"]
                        state_changed = True

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
                    "raw_signals": pos.get("raw_signals", {}),
                })
                state.balance += (pos.get("margin", 0.0) + pnl)
                state.last_exit_info[symbol] = (time.time(), pnl)
                closed_any = True
                state_changed = True
                continue

            # Position remains open
            pos["pnl"] = (current_price - pos["entry"]) * pos["size"] if side == "Buy" else (pos["entry"] - current_price) * pos["size"]
            new_positions.append(pos)

        if closed_any:
            state.positions = new_positions
    
    # Save only if something actually changed (closes or ratchets)
    if state_changed:
        state.save_account()

    # Process I/O (exits) outside the lock to avoid blocking
    for ex in exits_to_process:
        symbol = ex["symbol"]
        save_sim_cooldowns()
        state.slot_available_event.set()

        tui_log(f"{ex['exit_reason'].upper()} HIT: {symbol} closed at {ex['exit_price']}")
        
        if ex['pnl'] > 0:
            hw.bridge.signal('TP')
        else:
            hw.bridge.signal('SL')

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
            raw_signals = ex.get('raw_signals', {}),
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
            "CANDLES": 500
        }

        fresh_result = scanner.analyse(ticker, cfg, enable_ai=False, enable_entity=False)

        if not fresh_result:
            tui_log(f"FAIL: {symbol} no longer qualifies at step {i+1}")
            return None

        fresh_score = fresh_result["score"]

        # Spread check: avoid illiquid assets that may have fake signals
        current_spread = fresh_result.get("spread")
        spread_ok, spread_reason = pc.check_spread_filter(current_spread, symbol)
        if not spread_ok:
            tui_log(f"FAIL: {symbol} verification aborted — {spread_reason}")
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

def _calculate_dynamic_cooldown(pnl: float, entropy_penalty: int) -> int:
    """Calculates a dynamic cooldown period in seconds based on performance and market conditions."""
    if pnl >= 0:
        # Short fixed cooldown for wins, no reduction
        return BASE_COOLDOWN_WIN_S
    
    # Longer cooldown for losses, scaled by PnL
    loss_penalty = abs(pnl) * PNL_COOLDOWN_MULTIPLIER
    cooldown = BASE_COOLDOWN_LOSS_S + loss_penalty

    # Reduce cooldown if market is hot (high entropy)
    reduction = entropy_penalty * ENTROPY_COOLDOWN_REDUCTION_F
    
    final_cooldown = max(0, cooldown - reduction)
    return min(final_cooldown, MAX_COOLDOWN_S)

def execute_sim_setup(result: dict, direction: str) -> bool:
    """
    Opens a new simulated position or scales into an existing one.
    Returns True on success, False if the trade is skipped.
    """
    symbol = result["inst_id"]
    price  = result["price"]
    score  = result["score"]
    
    was_scale_in = False

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
        existing_pos = next((p for p in state.positions if p["symbol"] == symbol), None)
        
        if existing_pos:
            was_scale_in = True
            if existing_pos.get("margin", 0.0) >= MAX_MARGIN_PER_SYMBOL:
                tui_log(f"SKIP: {symbol} at MAX MARGIN (${existing_pos['margin']:.2f})", event_type="SKIP")
                return False
            # If scaling in, we skip the cooldown check since we already have skin in the game
        else:
            last_exit_info = state.last_exit_info.get(symbol)
            if last_exit_info:
                last_exit_timestamp, last_pnl = last_exit_info
                
                # Dynamic cooldown calculation
                dynamic_cooldown = _calculate_dynamic_cooldown(last_pnl, state.entropy_penalty)
                
                if time.time() - last_exit_timestamp < dynamic_cooldown:
                    remaining_s = dynamic_cooldown - (time.time() - last_exit_timestamp)
                    tui_log(f"COOLDOWN: {symbol} — {remaining_s/60:.1f}m remaining (PnL: {last_pnl:.2f}, Penalty: {state.entropy_penalty})", event_type="SKIP")
                    return False

        # ── Correlation/Overlap Gate (Idea 3) ──────────────────
        if _CM_OK:
            blocked, reason = corr_mgr.correlation_mgr.should_block_entry(symbol, direction, state.positions)
            if blocked:
                tui_log(f"CORR GATE: {symbol} blocked — {reason}", event_type="SKIP")
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
            available_liquidity = result.get("depth"),
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

    side     = "Buy" if direction == "LONG" else "Sell"
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
        "spread":        result.get("spread"),
        "raw_signals":   result.get("raw_signals", {}),
    }

    # Final state update: atomic append and balance deduction
    with state.lock:
        # Re-check for existing position inside final lock to avoid races
        current_pos = next((p for p in state.positions if p["symbol"] == symbol), None)
        
        if current_pos:
            # --- SCALE IN LOGIC ---
            old_size   = current_pos["size"]
            old_entry  = current_pos["entry"]
            new_size   = size
            new_entry  = entry_price
            
            total_size = old_size + new_size
            # Weighted average entry
            avg_entry  = (old_entry * old_size + new_entry * new_size) / total_size
            
            current_pos["size"]        = total_size
            current_pos["entry"]       = avg_entry
            current_pos["margin"]     += margin_to_use
            current_pos["fee"]        += fee
            current_pos["stop_price"]  = stop_px
            current_pos["take_profit"] = tp_px
            current_pos["trail_dist"]  = trail_dist
            # Reset watermarks to new avg entry for fresh trailing
            current_pos["high_water"]  = avg_entry if direction == "LONG" else 0.0
            current_pos["low_water"]   = avg_entry if direction == "SHORT" else 9_999_999.0
            
            tui_log(f"SCALED-IN: {symbol} | New Avg Entry: {avg_entry:.6g} | Total Margin: ${current_pos['margin']:.2f}", event_type="SCALE")
        else:
            # --- NEW POSITION ---
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
                "raw_signals":   result.get("raw_signals", {}),
            }
            state.positions.append(new_pos)
        
        state.balance -= (margin_to_use + fee)
    
    # Save outside the state.lock to avoid nested lock deadlock
    state.save_account()

    arrow = "▲ LONG" if direction == "LONG" else "▼ SHORT"
    action_label = "SCALED-IN" if was_scale_in else "ENTERED"
    tui_log(f"{action_label} {arrow} {symbol} @ {entry_price:.6g} (Score: {score}) slippage={slippage_amt:.6g}")

    # --- Entry Cinematic ---
    hw.bridge.signal('ENTRY')
    if direction == "LONG":
        play_animation(animations.long)
    else:
        play_animation(animations.short)

    emoji = "➕" if was_scale_in else ("🚀" if direction == "LONG" else "📉")
    msg_title = "SIM TRADE SCALED-IN" if was_scale_in else "SIM TRADE OPENED"
    send_telegram_message(
        f"{emoji} *{msg_title}*\n\n"
        f"*Symbol:* {symbol}\n"
        f"*Direction:* {direction}\n"
        f"*Price:* {price}\n"
        f"*Score:* {score}\n"
        f"*Time:* {datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S')}"
    )

    p_bot.log_trade({
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "symbol":    symbol,
        "direction": direction,
        "price":     price,
        "qty":       str(size),
        "score":     score,
        "status":    "simulated_scale_in" if was_scale_in else "simulated_entry",
    })

    _subscribe_symbol(symbol)
    _ensure_ws_started()

    with state.lock:
        if not state.display_thread_running and not getattr(args, 'no_tui', False):
            state.display_thread_running = True
            # REF: [Tier 1] Critical Thread Error Handling
            def _display_wrapper():
                import traceback
                try:
                    _live_pnl_display()
                except Exception as error:
                    logger.error(f"Display thread crashed: {error}\n{traceback.format_exc()}")
            threading.Thread(target=_display_wrapper, daemon=True).start()

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

def _get_cluster_threshold_penalty(intensity: float) -> float:
    """Returns a score penalty based on Hawkes intensity (λ)."""
    if intensity > HAWKES_INTENSITY_CRITICAL:
        return 0.50  # Major cluster
    if intensity > HAWKES_INTENSITY_HIGH:
        return 0.30
    if intensity > HAWKES_INTENSITY_MID:
        return 0.15
    return 0.0

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
    
    predictive_score = scan_res.get("predictive_score", 0.0)
    effective_fast_track = p_bot.FAST_TRACK_PREDICTIVE_SCORE + hawkes_penalty + current_penalty
    if predictive_score < effective_fast_track:
        if hawkes_penalty > 0 or current_penalty > 0:
            tui_log(f"FT THROTTLE: {scan_res['inst_id']} pred_score {predictive_score:.2f} < dynamic FT threshold {effective_fast_track:.2f} (λ={intensity:.2f}, H_pen={current_penalty:.2f})")
        return

    # Signal passed! Now update the tracker to throttle the NEXT one in this cluster.
    intensity = tracker.update(event_occurred=True)

    # Move position count and balance check inside state.lock for atomicity
    with state.lock:
        current_positions = state.positions
        acc_balance = state.balance

        # Gate on free margin instead of position count
        pending_margin = len(state.fast_track_opened) * (p_bot.MARGIN_USDT * 1.05)  # buffer for in-flight verifications
        used_margin = sum(p.get("margin", 0.0) for p in current_positions)
        effective_free = acc_balance - used_margin - pending_margin
        
        if effective_free < MIN_FREE_MARGIN:
            return

        current_syms = {p["symbol"] for p in current_positions}
        if scan_res["inst_id"] in current_syms or scan_res["inst_id"] in state.fast_track_opened:
            return

        if predictive_score < p_bot.FAST_TRACK_PREDICTIVE_SCORE: # redundant but safe
            return

        last_ft = state.fast_track_cooldowns.get(scan_res["inst_id"], 0)
        if time.time() - last_ft < FAST_TRACK_COOLDOWN_SECONDS:
            return

        state.fast_track_opened.add(scan_res["inst_id"])
        state.fast_track_cooldowns[scan_res["inst_id"]] = time.time()

    tui_log(f"⚡ FAST-TRACK: {scan_res['inst_id']} pred_score {predictive_score:.2f}! (λ={intensity:.2f})")

    # ── Wait & Verify ────────────────────────────────────
    try:
        verified_result = verify_sim_candidate(scan_res["inst_id"], direction, predictive_score)
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
    """
    The main scan-and-execute loop for the simulation bot.
    Periodically scans the market, evaluates signals, and manages simulated positions.
    Exits gracefully when the global _running flag is False.
    """
    global COOLDOWN_SECONDS
    state.no_tui = getattr(args, 'no_tui', False)

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
        "CANDLES":        500,
    }

    _ensure_ws_started()
    state.load_account()
    load_sim_cooldowns()

    with state.lock:
        balance = state.balance
        locked_margin = sum(p.get("margin", 0.0) for p in state.positions)
        current_upnl = sum(p.get("pnl", 0.0) for p in state.positions)
        state.equity_history.append(balance + locked_margin + current_upnl)

    # --- Cinematic Boot ---
    if not state.no_tui:
        play_animation(animations.boot)
    hw.bridge.signal('START')

    with state.lock:
        for p in state.positions:
            _subscribe_symbol(p["symbol"])

    with state.lock:
        if not state.display_thread_running:
            state.display_thread_running = True
            # REF: [Tier 1] Critical Thread Error Handling
            def _display_wrapper():
                import traceback
                try:
                    _live_pnl_display()
                except Exception as error:
                    logger.error(f"Display thread crashed: {error}\n{traceback.format_exc()}")
            threading.Thread(target=_display_wrapper, daemon=True).start()

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

            update_pnl_and_stops(args)

            # Recompute dynamic max positions and available slots
            # REF: Tier 3: Non-Descriptive Variable Naming (acc -> account)
            _              = load_paper_account()

            # No slot cap — scan every cycle, gate on margin at execution time
            tui_log(f"Scanning LIVE market ({args.timeframe})...")
            state.display_paused_event.set()
            t0 = time.time()
            long_r, short_r = p_bot.run_scanner_both(cfg, args, on_result=on_scan_result)
            elapsed = time.time() - t0
            state.display_paused_event.clear()
            tui_log(f"Scan complete in {elapsed:.1f}s — L: {len(long_r)}  S: {len(short_r)}")

            _process_animations()

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
                symbols_in_position=in_pos_updated,
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
                
                _process_animations()
            else:
                tui_log("No qualifying setups found.")

            sleep_interval = args.interval
            # Wait for either timeout, slot availability, or manual trigger
            wait_start = time.time()
            while time.time() - wait_start < sleep_interval:
                if state.force_scan_event.is_set():
                    state.force_scan_event.clear()
                    break
                if state.slot_available_event.wait(timeout=1.0):
                    state.slot_available_event.clear()
                    break

        except Exception as e:
            logger.error(f"Sim bot loop error: {e}. Backing off for 30s...")
            import traceback
            logger.debug(traceback.format_exc())
            time.sleep(30)

    tui_log("Simulation bot shutdown requested. Cleaning up...", event_type="HALT")
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


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Parses arguments and starts the simulation bot."""
    parser = argparse.ArgumentParser(description="Phemex Sim Bot (Paper Trading)")
    parser.add_argument("--interval",       type=int,   default=300)
    parser.add_argument("--min-score",      type=int,   default=120)
    parser.add_argument("--min-score-gap",  type=int,   default=0)
    parser.add_argument("--direction",      default="SHORT", choices=["LONG", "SHORT", "BOTH"])
    parser.add_argument("--timeframe",      default="4H")
    parser.add_argument("--cooldown",       type=int,   default=5, help="Cooldown in candles after exit")
    parser.add_argument("--min-vol",        type=int,   default=1_000_000)
    parser.add_argument("--workers",        type=int,   default=30)
    parser.add_argument("--rate",           type=float, default=20.0)
    parser.add_argument("--no-ai",          action="store_true")
    parser.add_argument("--no-entity",      action="store_true")
    parser.add_argument("--no-dynamic",     action="store_true")
    parser.add_argument("--textual",        action="store_true", help="Use modern Textual TUI dashboard")
    parser.add_argument("--web",            action="store_true", help="Enable web dashboard backend")
    parser.add_argument("--web-port",       type=int, default=8000, help="Port for web dashboard backend")
    parser.add_argument("--no-tui",         action="store_true", help="Run without the full-screen TUI dashboard")
    args = parser.parse_args()

    print(Fore.GREEN + Style.BRIGHT + "  🚀 Phemex SIMULATION Bot Starting (Paper Trading)")
    print("  Market   : LIVE (api.phemex.com)")
    print("  Account  : LOCAL (paper_account.json)")
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
            get_logs_fn        = _get_tui_logs,
            run_scan_fn        = lambda: _manual_tg_scan(args),
            get_chart_fn       = _get_session_chart,
            run_backtest_fn    = lambda txt: _run_manual_backtest(txt, args)
        )
        logger.info("telegram_controller: started")

    # ── Web Bridge ────────────────────────────────────────────────────────────
    if args.web:
        web_bridge.start_bridge_thread(state, _bot_logs, port=args.web_port)
        logger.info(f"web_bridge: started on port {args.web_port}")

    try:
        if args.textual and not args.no_tui:
            # Run bot loop in a background thread, while Textual takes the main thread
            bot_thread = threading.Thread(target=sim_bot_loop, args=(args,), daemon=True)
            bot_thread.start()
            
            # Launch Textual App (blocking)
            app = FancyBotApp(bot_state=state, bot_logs=_bot_logs, initial_balance=INITIAL_BALANCE)
            app.run()
            
            # If app.run() returns, the user quit the UI
            _running = False
        else:
            sim_bot_loop(args)
    finally:
        if not _running or _shutdown_requested:
            print(Fore.YELLOW + "\n  Bot stopped. Shutting down...")
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
