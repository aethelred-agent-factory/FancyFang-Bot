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
Daily Drawdown Kill Switch — Upgrade #7 (Refactored)
=====================================================
Tracks intraday PnL and halts NEW trade entry if daily loss exceeds threshold.
Refactored into a class to eliminate global state and improve testability.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Optional

logger = logging.getLogger("drawdown_guard")
logger.addHandler(logging.NullHandler())

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
MAX_DAILY_DRAWDOWN = float(os.getenv("MAX_DAILY_DRAWDOWN", "0.05"))   # 5 %

@dataclass
class DrawdownState:
    day: Optional[str] = None
    start_balance: float = 0.0
    daily_pnl: float = 0.0
    killed: bool = False
    kill_reason: str = ""
    kill_count_today: int = 0

class DrawdownGuard:
    """Thread-safe Drawdown Guard with automatic UTC-midnight reset."""

    def __init__(self, max_drawdown: float = MAX_DAILY_DRAWDOWN):
        self._lock = threading.Lock()
        self._max_drawdown = max_drawdown
        self._state = DrawdownState()

    def _today(self) -> str:
        return datetime.datetime.utcnow().strftime("%Y-%m-%d")

    def _maybe_reset(self, current_balance: float) -> None:
        """Reset state if we've crossed into a new UTC day."""
        today = self._today()
        if self._state.day != today:
            prev_day = self._state.day
            prev_pnl = self._state.daily_pnl
            prev_kill = self._state.killed
            
            self._state = DrawdownState(
                day=today,
                start_balance=current_balance
            )
            
            if prev_day is not None:
                logger.info(
                    f"drawdown_guard: new day {today} — prev day {prev_day} "
                    f"pnl={prev_pnl:+.4f}, was_killed={prev_kill}"
                )

    def _check_kill(self) -> None:
        """Check if the kill switch should be activated and update state."""
        if self._state.killed or self._state.start_balance <= 0:
            return

        loss_pct = -self._state.daily_pnl / self._state.start_balance
        if loss_pct >= self._max_drawdown:
            self._state.killed = True
            self._state.kill_count_today += 1
            self._state.kill_reason = (
                f"Daily loss {loss_pct:.2%} ≥ threshold {self._max_drawdown:.2%} "
                f"(start={self._state.start_balance:.2f}, pnl={self._state.daily_pnl:+.4f})"
            )
            logger.warning(f"drawdown_guard: KILL SWITCH ACTIVATED — {self._state.kill_reason}")

    def record_pnl(self, pnl: float, current_balance: float = 0.0) -> None:
        """Record a closed trade's PnL."""
        with self._lock:
            self._maybe_reset(current_balance)
            self._state.daily_pnl += pnl
            self._check_kill()
            logger.debug(f"drawdown_guard: pnl={pnl:+.4f}, daily_pnl={self._state.daily_pnl:+.4f}")

    def set_start_balance(self, balance: float) -> None:
        """Explicitly set today's starting balance."""
        with self._lock:
            self._maybe_reset(balance)
            if self._state.start_balance <= 0:
                self._state.start_balance = balance
                logger.info(f"drawdown_guard: start_balance set to {balance:.4f}")
            self._check_kill()

    def can_open_trade(self, current_balance: float = 0.0) -> Tuple[bool, str]:
        """Returns (True, "") if new trades are allowed."""
        with self._lock:
            self._maybe_reset(current_balance)
            if self._state.killed:
                return False, self._state.kill_reason
            return True, ""

    def get_status(self) -> Dict[str, Any]:
        """Return a snapshot of the current daily drawdown state."""
        with self._lock:
            s = self._state.__dict__.copy()
            s["loss_pct"] = round(-s["daily_pnl"] / s["start_balance"] if s["start_balance"] > 0 else 0.0, 6)
            s["threshold"] = self._max_drawdown
            s["remaining"] = round(max(0.0, s["start_balance"] * self._max_drawdown + s["daily_pnl"]), 4)
            return s

    def force_reset(self, new_balance: float = 0.0) -> None:
        """Manually reset the kill switch."""
        with self._lock:
            self._state = DrawdownState(
                day=self._today(),
                start_balance=new_balance
            )
        logger.info(f"drawdown_guard: manually reset. new balance={new_balance:.4f}")

# Singleton for legacy module-level access
_instance = DrawdownGuard()

def record_pnl(*args, **kwargs): return _instance.record_pnl(*args, **kwargs)
def set_start_balance(*args, **kwargs): return _instance.set_start_balance(*args, **kwargs)
def can_open_trade(*args, **kwargs): return _instance.can_open_trade(*args, **kwargs)
def get_status(): return _instance.get_status()
def force_reset(*args, **kwargs): return _instance.force_reset(*args, **kwargs)

# Constant exposure
MAX_DAILY_DRAWDOWN = _instance._max_drawdown
