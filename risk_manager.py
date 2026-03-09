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
Portfolio Risk Manager — Upgrade #12 (Refactored)
==================================================
Replaces fixed-% risk sizing with dynamic, account-aware position sizing.
Refactored into a class to eliminate global state and improve testability.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("risk_manager")
logger.addHandler(logging.NullHandler())

# ─────────────────────────────────────────────────────────────────────────────
# Configuration Constants
# ─────────────────────────────────────────────────────────────────────────────
RISK_MODEL           = os.getenv("RISK_MODEL",           "dynamic_kelly")
FIXED_RISK_PER_TRADE = float(os.getenv("FIXED_RISK_PER_TRADE", "1.0"))
RISK_PCT_PER_TRADE   = float(os.getenv("RISK_PCT_PER_TRADE",   "0.01"))   # 1 %
MAX_PORTFOLIO_RISK   = float(os.getenv("MAX_PORTFOLIO_RISK",   "0.30"))   # 30 %
MAX_POSITIONS         = int(os.getenv("MAX_POSITIONS",          "3"))
MIN_ACCOUNT_RISK_PCT = float(os.getenv("MIN_ACCOUNT_RISK_PCT", "0.005"))  # 0.5 %
MAX_ACCOUNT_RISK_PCT = float(os.getenv("MAX_ACCOUNT_RISK_PCT", "0.05"))   # 5 %

_SMALL_ACCOUNT_THRESHOLD = 50.0
_LARGE_ACCOUNT_THRESHOLD = 500.0

@dataclass
class PerformanceStats:
    wins: float = 0.0
    losses: float = 0.0
    gross_wins: float = 0.0
    gross_loss: float = 0.0

class RiskManager:
    """Thread-safe Risk Manager for position sizing and portfolio protection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._perf = PerformanceStats()

    def record_trade_result(self, pnl: float) -> None:
        """Update internal win/loss rolling statistics."""
        with self._lock:
            if pnl > 0:
                self._perf.wins += 1
                self._perf.gross_wins += pnl
            else:
                self._perf.losses += 1
                self._perf.gross_loss += abs(pnl)
        logger.debug(f"risk_manager: recorded trade PnL={pnl:+.4f}")

    def get_open_position_risk(self, open_positions: List[Dict[str, Any]]) -> float:
        """Estimate the total current risk (in USD) committed to open positions."""
        # REF: [Tier 3] Descriptive Naming
        total_risk = 0.0
        for position in open_positions:
            margin = float(position.get("margin", 0.0))
            total_risk += margin
        return total_risk

    def _adaptive_risk_pct(self, account_balance: float) -> float:
        """Returns a risk % that scales inversely with account size."""
        if account_balance <= _SMALL_ACCOUNT_THRESHOLD:
            return MAX_ACCOUNT_RISK_PCT
        if account_balance >= _LARGE_ACCOUNT_THRESHOLD:
            return MIN_ACCOUNT_RISK_PCT
        
        ratio = (account_balance - _SMALL_ACCOUNT_THRESHOLD) / (
            _LARGE_ACCOUNT_THRESHOLD - _SMALL_ACCOUNT_THRESHOLD
        )
        return MAX_ACCOUNT_RISK_PCT - ratio * (MAX_ACCOUNT_RISK_PCT - MIN_ACCOUNT_RISK_PCT)

    def _kelly_risk_amount(self, account_balance: float, signal_confidence: float) -> float:
        """Half-Kelly risk sizing based on rolling trade history."""
        # REF: [Tier 3] Descriptive Naming
        with self._lock:
            wins         = self._perf.wins
            losses       = self._perf.losses
            gross_wins   = self._perf.gross_wins
            gross_losses = self._perf.gross_loss

        total_trades = wins + losses
        if total_trades < 10:
            base_percentage = self._adaptive_risk_pct(account_balance)
            confidence_scalar = 0.5 + 0.5 * max(0.0, min(1.0, signal_confidence))
            return account_balance * base_percentage * confidence_scalar

        win_rate = wins / total_trades
        average_win  = gross_wins / wins if wins > 0 else 0.0
        average_loss = gross_losses / losses if losses > 0 else 0.0

        if average_loss == 0 or average_win == 0:
            return account_balance * self._adaptive_risk_pct(account_balance)

        win_loss_ratio = average_win / average_loss
        loss_rate      = 1.0 - win_rate
        kelly_fraction = (win_rate * win_loss_ratio - loss_rate) / win_loss_ratio

        if kelly_fraction <= 0:
            return account_balance * MIN_ACCOUNT_RISK_PCT

        confidence_scalar = 0.5 + 0.5 * max(0.0, min(1.0, signal_confidence))
        fraction = 0.5 * confidence_scalar
        risk_amount = account_balance * kelly_fraction * fraction

        return min(risk_amount, account_balance * MAX_ACCOUNT_RISK_PCT)

    def compute_dynamic_risk(
        self,
        account_balance: float,
        signal_strength: float,
        stop_distance: Optional[float] = None,
        open_positions: Optional[List[Dict[str, Any]]] = None,
        risk_model: Optional[str] = None,
    ) -> Tuple[float, float]:
        """Compute the risk amount (USD) and resulting position size for a new trade."""
        model = (risk_model or RISK_MODEL).lower()
        open_positions = open_positions or []

        if signal_strength > 1.0:
            signal_confidence = min(1.0, (signal_strength - 100) / 100.0)
        else:
            signal_confidence = max(0.0, min(1.0, signal_strength))

        if model == "fixed_usd":
            risk_amount = FIXED_RISK_PER_TRADE
        elif model == "percent_of_account":
            risk_amount = account_balance * self._adaptive_risk_pct(account_balance)
        else:
            risk_amount = self._kelly_risk_amount(account_balance, signal_confidence)

        current_risk = self.get_open_position_risk(open_positions)
        portfolio_cap = account_balance * MAX_PORTFOLIO_RISK
        remaining_capacity = max(0.0, portfolio_cap - current_risk)

        risk_amount = max(0.0, min(risk_amount, remaining_capacity))
        
        position_size = 0.0
        if stop_distance and stop_distance > 0 and risk_amount > 0:
            position_size = risk_amount / stop_distance

        return risk_amount, position_size

    def should_reject_trade(
        self,
        risk_amount: float,
        account_balance: float,
        open_positions: List[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        """Final gate: reject the trade if portfolio risk would be exceeded."""
        if len(open_positions) >= MAX_POSITIONS:
            return True, f"MAX_POSITIONS={MAX_POSITIONS} reached"

        current_risk  = self.get_open_position_risk(open_positions)
        portfolio_cap = account_balance * MAX_PORTFOLIO_RISK
        if current_risk + risk_amount > portfolio_cap:
            return True, f"Portfolio risk cap ({MAX_PORTFOLIO_RISK:.0%}) exceeded"

        return False, ""

# Singleton instance for legacy module-level access
_instance = RiskManager()

def record_trade_result(pnl: float) -> None:
    _instance.record_trade_result(pnl)

def compute_dynamic_risk(*args, **kwargs) -> Tuple[float, float]:
    return _instance.compute_dynamic_risk(*args, **kwargs)

def should_reject_trade(*args, **kwargs) -> Tuple[bool, str]:
    return _instance.should_reject_trade(*args, **kwargs)

def get_open_position_risk(open_positions: List[Dict[str, Any]]) -> float:
    return _instance.get_open_position_risk(open_positions)
