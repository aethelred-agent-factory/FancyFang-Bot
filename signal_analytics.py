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
Signal Analytics — Upgrade #8
================================
Tracks per-signal-type performance statistics across all trades.
Lightweight, no external dependencies beyond stdlib + numpy.

Data is stored in signal_analytics.json next to this file.
All methods are thread-safe.

Metrics computed per signal type:
  - trade_count
  - win_count / loss_count
  - win_rate          : wins / trades
  - avg_return        : mean PnL over all trades
  - expectancy        : win_rate * avg_win - loss_rate * avg_loss
  - profit_factor     : gross_wins / gross_losses
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("signal_analytics")
logger.addHandler(logging.NullHandler())

_ANALYTICS_FILE = Path(__file__).parent / "signal_analytics.json"
_lock = threading.Lock()

# ── In-memory cache (T3-07) ──────────────────────────────────────────
# Loaded once from disk on first access; flushed to disk every FLUSH_EVERY trades.
_cache: Optional[Dict[str, Any]] = None
_dirty_count: int = 0
FLUSH_EVERY: int = 5  # flush to disk after this many writes


def _ensure_loaded() -> Dict[str, Any]:
    """Return the in-memory cache, loading from disk on first call."""
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def _flush_if_needed(force: bool = False) -> None:
    """Flush in-memory cache to disk if dirty count threshold reached."""
    global _dirty_count
    _dirty_count += 1
    if force or _dirty_count >= FLUSH_EVERY:
        if _cache is not None:
            _save(_cache)
        _dirty_count = 0


def flush() -> None:
    """Force an immediate flush of the in-memory cache to disk."""
    with _lock:
        _flush_if_needed(force=True)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> Dict[str, Any]:
    """Load analytics data from disk.  Returns empty dict on any error."""
    try:
        if _ANALYTICS_FILE.exists():
            return json.loads(_ANALYTICS_FILE.read_text())
    except Exception as e:
        logger.warning(f"signal_analytics: load error — {e}")
    return {}


def _save(data: Dict[str, Any]) -> None:
    """Atomically persist analytics data to disk."""
    try:
        tmp = _ANALYTICS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_ANALYTICS_FILE)
    except Exception as e:
        logger.error(f"signal_analytics: save error — {e}")


def _default_bucket() -> Dict[str, Any]:
    return {
        "trade_count":  0,
        "win_count":    0,
        "loss_count":   0,
        "gross_wins":   0.0,
        "gross_losses": 0.0,   # stored as positive number
        "pnl_list":     [],    # last 500 individual PnL values for stddev/expectancy
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def record_trade(
    signal_types: List[str],
    entry_price: float,
    exit_price: float,
    pnl: float,
    direction: str,
    symbol: str,
) -> None:
    """
    Record a completed trade against each signal type that contributed to it.

    Args:
        signal_types : list of signal label strings (e.g. ["RSI Recovery", "BB Lower"])
        entry_price  : actual fill price on entry
        exit_price   : actual fill price on exit
        pnl          : net PnL in account currency (after fees/slippage)
        direction    : "LONG" or "SHORT"
        symbol       : instrument symbol
    """
    if not signal_types:
        signal_types = ["UNKNOWN"]

    with _lock:
        data = _ensure_loaded()
        signals_node = data.setdefault("signals", {})

        for sig in signal_types:
            bucket = signals_node.setdefault(sig, _default_bucket())
            bucket["trade_count"] += 1
            # Keep pnl_list bounded to last 500
            bucket.setdefault("pnl_list", []).append(round(pnl, 6))
            if len(bucket["pnl_list"]) > 500:
                bucket["pnl_list"] = bucket["pnl_list"][-500:]

            if pnl > 0:
                bucket["win_count"]  += 1
                bucket["gross_wins"] += pnl
            else:
                bucket["loss_count"]   += 1
                bucket["gross_losses"] += abs(pnl)

        # Also append to a flat trade log (last 1000)
        trade_log = data.setdefault("trade_log", [])
        trade_log.append({
            "symbol":       symbol,
            "direction":    direction,
            "entry":        round(entry_price, 8),
            "exit":         round(exit_price, 8),
            "pnl":          round(pnl, 6),
            "signal_types": signal_types,
        })
        if len(trade_log) > 1000:
            data["trade_log"] = trade_log[-1000:]

        _flush_if_needed()

    logger.info(
        f"signal_analytics: recorded {direction} {symbol} | "
        f"PnL {pnl:+.4f} | signals: {signal_types}"
    )


def get_signal_stats() -> Dict[str, Dict[str, float]]:
    """
    Returns computed metrics per signal type.

    Returns dict keyed by signal name with:
        trade_count, win_rate, avg_return, expectancy, profit_factor
    """
    with _lock:
        data = _ensure_loaded()
    signals_node = data.get("signals", {})
    result: Dict[str, Dict[str, float]] = {}

    for sig, b in signals_node.items():
        tc = b.get("trade_count", 0)
        if tc == 0:
            continue

        wc = b.get("win_count", 0)
        lc = b.get("loss_count", 0)
        gw = b.get("gross_wins", 0.0)
        gl = b.get("gross_losses", 0.0)    # positive
        pnl_list = b.get("pnl_list", [])

        win_rate  = wc / tc if tc > 0 else 0.0
        loss_rate = lc / tc if tc > 0 else 0.0
        avg_win   = gw / wc if wc > 0 else 0.0
        avg_loss  = gl / lc if lc > 0 else 0.0

        expectancy     = win_rate * avg_win - loss_rate * avg_loss
        profit_factor  = gw / gl if gl > 0 else float("inf")
        avg_return     = sum(pnl_list) / len(pnl_list) if pnl_list else 0.0

        result[sig] = {
            "trade_count":    tc,
            "win_count":      wc,
            "loss_count":     lc,
            "win_rate":       round(win_rate, 4),
            "avg_return":     round(avg_return, 6),
            "expectancy":     round(expectancy, 6),
            "profit_factor":  round(profit_factor, 4),
            "gross_wins":     round(gw, 4),
            "gross_losses":   round(gl, 4),
        }

    return result


def print_signal_report() -> None:
    """Pretty-print the signal performance table to stdout."""
    stats = get_signal_stats()
    if not stats:
        print("No signal analytics data yet.")
        return

    col_w = [32, 8, 8, 10, 12, 12]
    header = (
        f"{'Signal':<{col_w[0]}} {'Trades':>{col_w[1]}} {'WinRate':>{col_w[2]}} "
        f"{'AvgReturn':>{col_w[3]}} {'Expectancy':>{col_w[4]}} {'ProfitFctr':>{col_w[5]}}"
    )
    sep = "-" * sum(col_w + [len(col_w) - 1])
    print("\n=== Signal Performance Report ===")
    print(sep)
    print(header)
    print(sep)
    for sig, m in sorted(stats.items(), key=lambda x: -x[1]["expectancy"]):
        print(
            f"{sig:<{col_w[0]}} "
            f"{m['trade_count']:>{col_w[1]}} "
            f"{m['win_rate']*100:>{col_w[2]}.1f}% "
            f"{m['avg_return']:>{col_w[3]}.4f} "
            f"{m['expectancy']:>{col_w[4]}.4f} "
            f"{m['profit_factor']:>{col_w[5]}.2f}"
        )
    print(sep + "\n")


def get_trade_log() -> List[Dict[str, Any]]:
    """
    Return the raw list of recorded trades.

    [T2-01] Use _ensure_loaded() (returns the in-memory _cache) instead of
    _load() (always reads from disk).  _load() bypasses the write buffer, so
    callers received a view that was missing up to FLUSH_EVERY-1 recent trades
    that had not yet been flushed to disk — creating a split-brain view between
    this function and get_signal_stats(), which both refer to the same dataset.
    """
    with _lock:
        data = _ensure_loaded()
        return list(data.get("trade_log", []))
