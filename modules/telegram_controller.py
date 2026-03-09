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
Telegram Control Interface — Upgrade #13
==========================================
Lightweight Telegram bot for controlling and monitoring the trading bot
via chat commands.  Runs as a daemon thread inside sim_bot or p_bot.

Commands:
  /start    — enable new trade entry (if kill switch was manually set)
  /stop     — halt new trade entry (manual kill switch)
  /status   — current bot status, balance, open positions, drawdown guard
  /profit   — session PnL summary (closed trades)
  /positions — list open positions with entry price and unrealised PnL

Setup:
  Set TG_BOT_TOKEN and TG_CHAT_ID in your .env file.
  Call telegram_controller.start(get_balance_fn, get_positions_fn)
  from your main bot loop.

The handler callbacks (get_balance_fn, get_positions_fn) are injected
at runtime so this module has zero circular imports.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger("tg_controller")
logger.addHandler(logging.NullHandler())

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID",   "")

_BASE = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

# ─────────────────────────────────────────────────────────────────────────────
# Internal state
# ─────────────────────────────────────────────────────────────────────────────
_lock        = threading.Lock()
_offset      = 0
_running     = False
_halted      = False   # manual /stop override
_thread: Optional[threading.Thread] = None

# Injected callbacks — set by start()
_get_balance:   Optional[Callable[[], float]]       = None
_get_positions: Optional[Callable[[], List[dict]]]  = None
_get_session_pnl: Optional[Callable[[], dict]]      = None    # optional


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send(text: str) -> None:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"{_BASE}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=8,
        )
    except Exception as e:
        logger.debug(f"tg_controller: send error — {e}")


def _get_updates() -> List[dict]:
    global _offset
    with _lock:
        current_offset = _offset
    try:
        r = requests.get(
            f"{_BASE}/getUpdates",
            params={"offset": current_offset, "timeout": 20, "allowed_updates": ["message"]},
            timeout=25,
        )
        data = r.json()
        updates = data.get("result", [])
        if updates:
            with _lock:
                _offset = updates[-1]["update_id"] + 1
        return updates
    except Exception as e:
        logger.debug(f"tg_controller: getUpdates error — {e}")
        return []


def is_halted() -> bool:
    """Returns True if /stop has been issued and not yet /start-ed."""
    with _lock:
        return _halted


# ─────────────────────────────────────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────────────────────────────────────

def _handle_start(chat_id: str) -> None:
    global _halted
    with _lock:
        _halted = False
    logger.info("tg_controller: /start received — trading re-enabled")
    _send("✅ *Bot STARTED* — new trades will be accepted.")


def _handle_stop(chat_id: str) -> None:
    global _halted
    with _lock:
        _halted = True
    logger.info("tg_controller: /stop received — trading halted")
    _send("🛑 *Bot STOPPED* — new trade entry disabled. Existing trades continue.")


def _handle_status(chat_id: str) -> None:
    try:
        import modules.drawdown_guard
        dd = drawdown_guard.get_status()
        dd_line = (
            f"Daily PnL: `{dd['daily_pnl']:+.4f}` USDT "
            f"({dd['loss_pct']*100:.2f}% of start)\n"
            f"Kill switch: `{'ACTIVE 🔴' if dd['killed'] else 'OFF 🟢'}`\n"
            f"Remaining room: `{dd['remaining']:.4f}` USDT"
        )
    except Exception:
        dd_line = "_Drawdown guard unavailable_"

    with _lock:
        halt_str = "🛑 HALTED (manual /stop)" if _halted else "🟢 ACTIVE"

    balance = _get_balance() if _get_balance else 0.0
    n_pos   = len(_get_positions()) if _get_positions else "?"

    msg = (
        "📊 *Bot Status*\n\n"
        f"Status: `{halt_str}`\n"
        f"Balance: `{balance:.4f}` USDT\n"
        f"Open positions: `{n_pos}`\n\n"
        f"{dd_line}"
    )
    _send(msg)


def _handle_profit(chat_id: str) -> None:
    if _get_session_pnl:
        stats = _get_session_pnl()
        wins   = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total  = wins + losses
        wr     = wins / total * 100 if total > 0 else 0.0
        pnl    = stats.get("total_pnl", 0.0)
        msg = (
            "💰 *Session Profit Summary*\n\n"
            f"Trades: `{total}` (W:`{wins}` / L:`{losses}`)\n"
            f"Win rate: `{wr:.1f}%`\n"
            f"Total PnL: `{pnl:+.4f}` USDT"
        )
    else:
        msg = "_Session PnL callback not registered._"
    _send(msg)


def _handle_positions(chat_id: str) -> None:
    positions = _get_positions() if _get_positions else []
    if not positions:
        _send("📭 *No open positions.*")
        return

    lines = ["📋 *Open Positions*\n"]
    for p in positions:
        sym   = p.get("symbol", "?")
        side  = p.get("side", "?")
        entry = p.get("entry", 0.0)
        pnl   = p.get("pnl", 0.0)
        score = p.get("entry_score", "?")
        arrow = "🟢▲" if side == "Buy" else "🔴▼"
        lines.append(
            f"{arrow} `{sym}` | Entry: `{entry:.5g}` | "
            f"PnL: `{pnl:+.4f}` | Score: `{score}`"
        )
    _send("\n".join(lines))


def _handle_block(chat_id: str, text: str) -> None:
    try:
        import modules.event_filter
        parts = text.split()
        mins = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
        event_filter.filter.block_manual(mins)
        _send(f"🛑 *Manual Block* — trading suppressed for {mins} minutes.")
    except Exception as e:
        _send(f"⚠️ Error: {e}")


def _handle_unblock(chat_id: str) -> None:
    try:
        import modules.event_filter
        event_filter.filter.unblock()
        _send("✅ *Manual Block CLEARED* — events/news filters still active.")
    except Exception as e:
        _send(f"⚠️ Error: {e}")


_COMMAND_MAP: Dict[str, Callable] = {
    "/start":     _handle_start,
    "/stop":      _handle_stop,
    "/status":    _handle_status,
    "/profit":    _handle_profit,
    "/positions": _handle_positions,
    "/block":     _handle_block,
    "/unblock":   _handle_unblock,
}


# ─────────────────────────────────────────────────────────────────────────────
# Poll loop
# ─────────────────────────────────────────────────────────────────────────────

def _poll_loop() -> None:
    global _running
    logger.info("tg_controller: poll loop started")
    while _running:
        updates = _get_updates()
        for upd in updates:
            msg = upd.get("message", {})
            raw_text = (msg.get("text") or "").strip().lower()
            if not raw_text:
                continue
            parts = raw_text.split()
            cmd = parts[0]
            chat_id = str(msg.get("chat", {}).get("id", ""))
            # Only respond to the configured chat ID (security gate)
            if TG_CHAT_ID and chat_id != str(TG_CHAT_ID):
                logger.warning(f"tg_controller: ignoring message from unknown chat {chat_id}")
                continue
            
            handler = _COMMAND_MAP.get(cmd)
            if handler:
                logger.info(f"tg_controller: handling command '{cmd}'")
                try:
                    if cmd == "/block":
                        handler(chat_id, raw_text)
                    else:
                        handler(chat_id)
                except Exception as e:
                    logger.error(f"tg_controller: handler error for '{cmd}': {e}")
            elif cmd.startswith("/"):
                _send(
                    f"Unknown command: `{cmd}`\n"
                    f"Available: /start /stop /status /profit /positions /block /unblock"
                )
        time.sleep(1)
    logger.info("tg_controller: poll loop stopped")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start(
    get_balance_fn:     Callable[[], float],
    get_positions_fn:   Callable[[], List[dict]],
    get_session_pnl_fn: Optional[Callable[[], dict]] = None,
) -> None:
    """
    Start the Telegram control interface in a daemon thread.

    Args:
        get_balance_fn      : callable returning current account balance (float)
        get_positions_fn    : callable returning list of open position dicts
        get_session_pnl_fn  : optional callable returning session stats dict
                              with keys: wins, losses, total_pnl
    """
    global _running, _thread, _get_balance, _get_positions, _get_session_pnl

    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.warning("tg_controller: TG_BOT_TOKEN or TG_CHAT_ID not set — Telegram control disabled")
        return

    _get_balance     = get_balance_fn
    _get_positions   = get_positions_fn
    _get_session_pnl = get_session_pnl_fn

    with _lock:
        if _running:
            logger.debug("tg_controller: already running")
            return
        _running = True

    _thread = threading.Thread(target=_poll_loop, daemon=True, name="tg_controller")
    _thread.start()
    logger.info("tg_controller: started")
    _send("🤖 *FancyBot online* — send /status for current state.")


def stop() -> None:
    """Stop the Telegram poll loop."""
    global _running
    with _lock:
        _running = False
    logger.info("tg_controller: stop requested")
