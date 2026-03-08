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
Portfolio Risk Manager — Upgrade #12
======================================
Replaces fixed-% risk sizing with dynamic, account-aware position sizing.

Supported RISK_MODEL values (set via env or direct call):
  "fixed_usd"           — always risk FIXED_RISK_PER_TRADE USD per position
  "percent_of_account"  — risk RISK_PCT_PER_TRADE % of account balance
  "dynamic_kelly"       — half-Kelly criterion scaled by signal confidence;
                          falls back to percent_of_account if not enough history

Key constants (all override-able via environment variables):
  RISK_MODEL            default: "dynamic_kelly"
  FIXED_RISK_PER_TRADE  default: 1.0   USD
  RISK_PCT_PER_TRADE    default: 0.01  (1 %)
  MAX_PORTFOLIO_RISK    default: 0.30  (30 % of account balance across all open positions)
  MAX_POSITIONS         default: 3
  MIN_ACCOUNT_RISK_PCT  default: 0.005 (0.5 % — floor for tiny accounts)
  MAX_ACCOUNT_RISK_PCT  default: 0.05  (5 %  — ceiling for large accounts)

Adaptive scaling rules:
  - Accounts < $50  → risk up to MAX_ACCOUNT_RISK_PCT
  - Accounts > $500 → risk down to MIN_ACCOUNT_RISK_PCT
  - Linear interpolation in between

All public functions are thread-safe.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("risk_manager")
logger.addHandler(logging.NullHandler())

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
RISK_MODEL           = os.getenv("RISK_MODEL",           "dynamic_kelly")
FIXED_RISK_PER_TRADE = float(os.getenv("FIXED_RISK_PER_TRADE", "1.0"))
RISK_PCT_PER_TRADE   = float(os.getenv("RISK_PCT_PER_TRADE",   "0.01"))   # 1 %
MAX_PORTFOLIO_RISK   = float(os.getenv("MAX_PORTFOLIO_RISK",   "0.30"))   # 30 %
MAX_POSITIONS        = int(os.getenv("MAX_POSITIONS",          "3"))
MIN_ACCOUNT_RISK_PCT = float(os.getenv("MIN_ACCOUNT_RISK_PCT", "0.005"))  # 0.5 %
MAX_ACCOUNT_RISK_PCT = float(os.getenv("MAX_ACCOUNT_RISK_PCT", "0.05"))   # 5 %

_SMALL_ACCOUNT_THRESHOLD = 50.0
_LARGE_ACCOUNT_THRESHOLD = 500.0

# ─────────────────────────────────────────────────────────────────────────────
# Internal state
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()

# Rolling performance history — fed by record_trade_result()
_perf: Dict[str, float] = {
    "wins":       0.0,
    "losses":     0.0,
    "gross_wins": 0.0,
    "gross_loss":  0.0,   # stored as positive total
}


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def record_trade_result(pnl: float) -> None:
    """
    Update the internal win/loss rolling statistics.
    Call this after every closed trade so Kelly can adapt.

    Args:
        pnl : net PnL in account currency (positive = win, negative = loss)
    """
    with _lock:
        if pnl > 0:
            _perf["wins"]       += 1
            _perf["gross_wins"] += pnl
        else:
            _perf["losses"]    += 1
            _perf["gross_loss"] += abs(pnl)
    logger.debug(f"risk_manager: recorded trade PnL={pnl:+.4f}")


def get_open_position_risk(
    open_positions: List[dict],
) -> float:
    """
    Estimate the total current risk (in USD) committed to open positions.

    Each position dict should contain at minimum:
        margin   : USD margin reserved
        stop_pct : percentage stop distance from entry (optional)
    """
    total_risk = 0.0
    for pos in open_positions:
        margin = float(pos.get("margin", 0.0))
        # Rough worst-case risk = margin (full margin at risk if leveraged stop blows past)
        total_risk += margin
    return total_risk


def _adaptive_risk_pct(account_balance: float) -> float:
    """
    Returns a risk % that scales inversely with account size.

    Small accounts → higher % (need more growth per trade to stay viable)
    Large accounts → lower % (capital preservation dominates)
    """
    if account_balance <= _SMALL_ACCOUNT_THRESHOLD:
        return MAX_ACCOUNT_RISK_PCT
    if account_balance >= _LARGE_ACCOUNT_THRESHOLD:
        return MIN_ACCOUNT_RISK_PCT
    # Linear interpolation
    ratio = (account_balance - _SMALL_ACCOUNT_THRESHOLD) / (
        _LARGE_ACCOUNT_THRESHOLD - _SMALL_ACCOUNT_THRESHOLD
    )
    return MAX_ACCOUNT_RISK_PCT - ratio * (MAX_ACCOUNT_RISK_PCT - MIN_ACCOUNT_RISK_PCT)


def _kelly_risk_amount(
    account_balance: float,
    signal_confidence: float,   # 0.0 – 1.0
) -> float:
    """
    Half-Kelly risk sizing based on rolling trade history.
    Falls back to adaptive % if insufficient history (<10 trades).

    Args:
        account_balance   : current wallet balance (USD)
        signal_confidence : normalized signal strength [0, 1]
    Returns:
        Risk amount in USD for this trade.
    """
    with _lock:
        wins   = _perf["wins"]
        losses = _perf["losses"]
        gw     = _perf["gross_wins"]
        gl     = _perf["gross_loss"]

    total = wins + losses
    if total < 10:
        # Not enough history — use adaptive % with confidence scaling
        base_pct = _adaptive_risk_pct(account_balance)
        confidence_scalar = 0.5 + 0.5 * max(0.0, min(1.0, signal_confidence))
        amount = account_balance * base_pct * confidence_scalar
        logger.debug(
            f"kelly_risk: insufficient history ({total} trades) — "
            f"adaptive pct={base_pct:.3%}, conf={signal_confidence:.2f}, amount={amount:.4f}"
        )
        return amount

    win_rate = wins / total
    avg_win  = gw / wins  if wins  > 0 else 0.0
    avg_loss = gl / losses if losses > 0 else 0.0

    if avg_loss == 0 or avg_win == 0:
        return account_balance * _adaptive_risk_pct(account_balance)

    b = avg_win / avg_loss
    q = 1.0 - win_rate
    kelly_f = (win_rate * b - q) / b

    if kelly_f <= 0:
        # Negative edge — use floor % to stay in the game
        logger.debug(f"kelly_risk: negative kelly_f={kelly_f:.4f} — using floor risk")
        return account_balance * MIN_ACCOUNT_RISK_PCT

    # Half-Kelly for safety; scale by signal confidence [0.5x … 1.0x]
    confidence_scalar = 0.5 + 0.5 * max(0.0, min(1.0, signal_confidence))
    fraction = 0.5 * confidence_scalar
    amount   = account_balance * kelly_f * fraction

    # Hard cap: never risk more than MAX_ACCOUNT_RISK_PCT in one trade
    amount = min(amount, account_balance * MAX_ACCOUNT_RISK_PCT)
    logger.debug(
        f"kelly_risk: win_rate={win_rate:.2%}, b={b:.2f}, kelly_f={kelly_f:.4f}, "
        f"fraction={fraction:.2f}, amount={amount:.4f}"
    )
    return amount


def compute_dynamic_risk(
    account_balance: float,
    signal_strength: float,        # raw signal score or normalized [0,1]
    stop_distance: Optional[float] = None,   # price distance to stop-loss
    open_positions: Optional[List[dict]] = None,
    risk_model: Optional[str] = None,
) -> Tuple[float, float]:
    """
    Compute the risk amount (USD) and resulting position size for a new trade.

    Args:
        account_balance : current wallet balance
        signal_strength : normalized signal confidence [0, 1] or raw score
        stop_distance   : price units from entry to stop-loss (for unit sizing)
        open_positions  : list of current open position dicts (for portfolio check)
        risk_model      : override RISK_MODEL for this call

    Returns:
        (risk_amount_usd, position_size_units)
        - risk_amount_usd : USD to risk on the trade (used as margin basis)
        - position_size_units : contracts/units if stop_distance provided, else 0
    """
    model = (risk_model or RISK_MODEL).lower()
    open_positions = open_positions or []

    # ── Normalize signal_strength to [0, 1] if it looks like a raw score ──
    if signal_strength > 1.0:
        signal_confidence = min(1.0, (signal_strength - 100) / 100.0)
    else:
        signal_confidence = max(0.0, min(1.0, signal_strength))

    # ── Choose risk model ──────────────────────────────────────────────────
    if model == "fixed_usd":
        risk_amount = FIXED_RISK_PER_TRADE
        logger.info(f"risk_manager [fixed_usd]: risk_amount={risk_amount:.4f}")

    elif model == "percent_of_account":
        base_pct    = _adaptive_risk_pct(account_balance)
        risk_amount = account_balance * base_pct
        logger.info(
            f"risk_manager [percent]: balance={account_balance:.2f}, "
            f"pct={base_pct:.3%}, risk_amount={risk_amount:.4f}"
        )

    else:  # dynamic_kelly (default)
        risk_amount = _kelly_risk_amount(account_balance, signal_confidence)
        logger.info(
            f"risk_manager [kelly]: balance={account_balance:.2f}, "
            f"conf={signal_confidence:.2f}, risk_amount={risk_amount:.4f}"
        )

    # ── Portfolio cap check ────────────────────────────────────────────────
    current_risk = get_open_position_risk(open_positions)
    portfolio_cap = account_balance * MAX_PORTFOLIO_RISK
    remaining_capacity = max(0.0, portfolio_cap - current_risk)

    if risk_amount > remaining_capacity:
        logger.warning(
            f"risk_manager: portfolio cap hit — "
            f"current_risk={current_risk:.2f}, cap={portfolio_cap:.2f}, "
            f"reducing risk_amount from {risk_amount:.4f} to {remaining_capacity:.4f}"
        )
        risk_amount = remaining_capacity

    # Floor: never risk negative or insanely small amounts
    risk_amount = max(0.0, risk_amount)

    # ── Compute position size from stop distance ───────────────────────────
    position_size = 0.0
    if stop_distance and stop_distance > 0 and risk_amount > 0:
        position_size = risk_amount / stop_distance

    logger.info(
        f"risk_manager: FINAL risk_amount={risk_amount:.4f} USD, "
        f"pos_size={position_size:.6f} units "
        f"(stop_dist={stop_distance}, model={model})"
    )
    return risk_amount, position_size


def should_reject_trade(
    risk_amount: float,
    account_balance: float,
    open_positions: List[dict],
) -> Tuple[bool, str]:
    """
    Final gate: reject the trade if portfolio risk would be exceeded.

    Returns:
        (rejected: bool, reason: str)
    """
    if len(open_positions) >= MAX_POSITIONS:
        msg = f"MAX_POSITIONS={MAX_POSITIONS} reached ({len(open_positions)} open)"
        logger.warning(f"risk_manager: REJECT — {msg}")
        return True, msg

    current_risk  = get_open_position_risk(open_positions)
    portfolio_cap = account_balance * MAX_PORTFOLIO_RISK
    if current_risk + risk_amount > portfolio_cap:
        msg = (
            f"portfolio risk cap ({MAX_PORTFOLIO_RISK:.0%}) exceeded: "
            f"current={current_risk:.2f} + new={risk_amount:.2f} > cap={portfolio_cap:.2f}"
        )
        logger.warning(f"risk_manager: REJECT — {msg}")
        return True, msg

    return False, ""
