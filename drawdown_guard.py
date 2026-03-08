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
Daily Drawdown Kill Switch — Upgrade #7
=========================================
Tracks intraday PnL and halts NEW trade entry if the daily loss
exceeds MAX_DAILY_DRAWDOWN (default 5 % of starting-day balance).

Existing open trades are allowed to close normally.

Thread-safe. Resets automatically at UTC midnight.

Public API:
  record_pnl(pnl)         — call after every trade close
  can_open_trade()        — returns (True, "") or (False, reason)
  get_status()            — returns dict with current day stats
  force_reset()           — manually reset (e.g. for testing)
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
from typing import Tuple

logger = logging.getLogger("drawdown_guard")
logger.addHandler(logging.NullHandler())

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
MAX_DAILY_DRAWDOWN = float(os.getenv("MAX_DAILY_DRAWDOWN", "0.05"))   # 5 %

# ─────────────────────────────────────────────────────────────────────────────
# Internal state
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()

_state = {
    "day":              None,    # date string "YYYY-MM-DD"
    "start_balance":    0.0,
    "daily_pnl":        0.0,
    "killed":           False,
    "kill_reason":      "",
    "kill_count_today": 0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def _maybe_reset(current_balance: float) -> None:
    """Reset state if we've crossed into a new UTC day."""
    today = _today()
    if _state["day"] != today:
        prev_day  = _state["day"]
        prev_pnl  = _state["daily_pnl"]
        prev_kill = _state["killed"]
        _state["day"]              = today
        _state["start_balance"]    = current_balance
        _state["daily_pnl"]        = 0.0
        _state["killed"]           = False
        _state["kill_reason"]      = ""
        _state["kill_count_today"] = 0
        if prev_day is not None:
            logger.info(
                f"drawdown_guard: new day {today} — previous day {prev_day} "
                f"pnl={prev_pnl:+.4f}, was_killed={prev_kill}"
            )


def _check_kill() -> None:
    """Check if the kill switch should be activated and update state."""
    if _state["killed"]:
        return   # already killed for today
    if _state["start_balance"] <= 0:
        return

    loss_pct = -_state["daily_pnl"] / _state["start_balance"]
    if loss_pct >= MAX_DAILY_DRAWDOWN:
        _state["killed"]      = True
        _state["kill_count_today"] += 1
        _state["kill_reason"] = (
            f"Daily loss {loss_pct:.2%} ≥ threshold {MAX_DAILY_DRAWDOWN:.2%} "
            f"(start={_state['start_balance']:.2f}, pnl={_state['daily_pnl']:+.4f})"
        )
        logger.warning(f"drawdown_guard: KILL SWITCH ACTIVATED — {_state['kill_reason']}")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def record_pnl(pnl: float, current_balance: float = 0.0) -> None:
    """
    Record a closed trade's PnL.

    Args:
        pnl              : net PnL of the just-closed trade (positive or negative)
        current_balance  : current account balance (used for day-reset calibration)
    """
    with _lock:
        _maybe_reset(current_balance)
        _state["daily_pnl"] += pnl
        _check_kill()
        logger.debug(
            f"drawdown_guard: pnl={pnl:+.4f}, daily_pnl={_state['daily_pnl']:+.4f}, "
            f"killed={_state['killed']}"
        )


def set_start_balance(balance: float) -> None:
    """
    Explicitly set today's starting balance (call once at bot startup or day reset).
    Also re-evaluates the kill switch in case losses were recorded before this call
    (e.g. bot restarted mid-day after already exceeding the daily loss limit).
    """
    with _lock:
        _maybe_reset(balance)
        if _state["start_balance"] <= 0:
            _state["start_balance"] = balance
            logger.info(f"drawdown_guard: start_balance set to {balance:.4f}")
        _check_kill()


def can_open_trade(current_balance: float = 0.0) -> Tuple[bool, str]:
    """
    Returns (True, "") if new trades are allowed.
    Returns (False, reason) if the daily kill switch is active.
    """
    with _lock:
        _maybe_reset(current_balance)
        if _state["killed"]:
            return False, _state["kill_reason"]
        return True, ""


def get_status() -> dict:
    """Return a snapshot of the current daily drawdown state."""
    with _lock:
        s = dict(_state)
    loss_pct = 0.0
    if s["start_balance"] > 0:
        loss_pct = -s["daily_pnl"] / s["start_balance"]
    s["loss_pct"]    = round(loss_pct, 6)
    s["threshold"]   = MAX_DAILY_DRAWDOWN
    s["remaining"]   = round(
        max(0.0, s["start_balance"] * MAX_DAILY_DRAWDOWN + s["daily_pnl"]), 4
    )
    return s


def force_reset(new_balance: float = 0.0) -> None:
    """Manually reset the kill switch (useful for testing or operator override)."""
    with _lock:
        _state["day"]              = _today()
        _state["start_balance"]    = new_balance
        _state["daily_pnl"]        = 0.0
        _state["killed"]           = False
        _state["kill_reason"]      = ""
        _state["kill_count_today"] = 0
    logger.info(f"drawdown_guard: manually reset. new start_balance={new_balance:.4f}")
