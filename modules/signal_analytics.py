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
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("signal_analytics")
logger.addHandler(logging.NullHandler())

_ANALYTICS_FILE = Path(__file__).parent / "signal_analytics.json"
_lock = threading.RLock()
_storage: Optional[Any] = None

# ── In-memory cache (T3-07) ──────────────────────────────────────────
# Loaded once from disk/db on first access; flushed to db every FLUSH_EVERY trades.
_cache: Optional[Dict[str, Any]] = None
_dirty_count: int = 0
FLUSH_EVERY: int = 5  # flush to disk after this many writes


def init_storage(storage: Any):
    """Initialize with a storage manager."""
    global _storage, _cache
    with _lock:
        _storage = storage
        _cache = _load()
        logger.info("signal_analytics: initialized with storage manager")


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
    """Load analytics data from storage or legacy disk. Returns empty dict on any error."""
    # 1. Try SQLite first
    if _storage:
        try:
            stats = _storage.load_signal_stats()
            hour_stats = _storage.load_hour_stats()
            history = _storage.get_trade_history(limit=1000)
            # Adapt history to the old trade_log format if needed
            trade_log = []
            for h in history:
                trade_log.append({
                    "symbol": h['symbol'],
                    "direction": h['direction'],
                    "entry": h['entry'],
                    "exit": h['exit'],
                    "pnl": h['pnl'],
                    "timestamp": h['timestamp'],
                    "signal_types": h.get('signals', [])
                })
            return {"signals": stats, "hours": hour_stats, "trade_log": trade_log[::-1]} # reverse to maintain chronological order
        except Exception as e:
            logger.error(f"signal_analytics: SQLite load error — {e}")

    # 2. Fallback to legacy JSON
    try:
        if _ANALYTICS_FILE.exists():
            return json.loads(_ANALYTICS_FILE.read_text())
    except Exception as e:
        logger.warning(f"signal_analytics: legacy load error — {e}")
    return {}


def _save(data: Dict[str, Any]) -> None:
    """Atomically persist analytics data to storage."""
    # 1. Save to SQLite
    if _storage:
        try:
            _storage.save_signal_stats(data.get("signals", {}))
            _storage.save_hour_stats(data.get("hours", {}))
            # Trade log is already handled by append_trade in StorageManager
            return
        except Exception as e:
            logger.error(f"signal_analytics: SQLite save error — {e}")

    # 2. Fallback to JSON
    try:
        tmp = _ANALYTICS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_ANALYTICS_FILE)
    except Exception as e:
        logger.error(f"signal_analytics: legacy save error — {e}")


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
    timestamp: Optional[str] = None,
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
        timestamp    : ISO timestamp of the trade entry
    """
    if not signal_types:
        signal_types = ["UNKNOWN"]

    import datetime
    if timestamp:
        try:
            dt = datetime.datetime.fromisoformat(timestamp)
            trade_hour = dt.hour
        except Exception:
            trade_hour = datetime.datetime.now(datetime.timezone.utc).hour
    else:
        trade_hour = datetime.datetime.now(datetime.timezone.utc).hour

    with _lock:
        data = _ensure_loaded()
        signals_node = data.setdefault("signals", {})
        hours_node   = data.setdefault("hours", {})

        # Record for each signal
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

        # Record for the hour
        h_bucket = hours_node.setdefault(trade_hour, _default_bucket())
        h_bucket["trade_count"] += 1
        h_bucket.setdefault("pnl_list", []).append(round(pnl, 6))
        if len(h_bucket["pnl_list"]) > 500:
            h_bucket["pnl_list"] = h_bucket["pnl_list"][-500:]

        if pnl > 0:
            h_bucket["win_count"] += 1
            h_bucket["gross_wins"] += pnl
        else:
            h_bucket["loss_count"] += 1
            h_bucket["gross_losses"] += abs(pnl)

        # Also append to a flat trade log (last 1000)
        trade_log = data.setdefault("trade_log", [])
        trade_log.append({
            "symbol":       symbol,
            "direction":    direction,
            "entry":        round(entry_price, 8),
            "exit":         round(exit_price, 8),
            "pnl":          round(pnl, 6),
            "timestamp":    timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "signal_types": signal_types,
        })
        if len(trade_log) > 1000:
            data["trade_log"] = trade_log[-1000:]

        _flush_if_needed()

    logger.info(
        f"signal_analytics: recorded {direction} {symbol} | "
        f"PnL {pnl:+.4f} | Hour {trade_hour} | signals: {signal_types}"
    )


def get_signal_stats() -> Dict[str, Any]:
    """
    Returns computed metrics per signal type and per hour.

    Returns dict with "signals" and "hours" keys.
    """
    with _lock:
        data = _ensure_loaded()
    
    def _compute_metrics(node):
        res = {}
        for key, b in node.items():
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

            res[key] = {
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
        return res

    return {
        "signals": _compute_metrics(data.get("signals", {})),
        "hours":   _compute_metrics(data.get("hours", {}))
    }


def print_signal_report() -> None:
    """Pretty-print the signal performance table to stdout."""
    full_stats = get_signal_stats()
    stats = full_stats["signals"]
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
    print(sep)

    # Hour stats
    h_stats = full_stats["hours"]
    if h_stats:
        print("\n=== Hour-of-Day Performance (UTC) ===")
        h_col_w = [8, 8, 8, 10, 12, 12]
        h_header = (
            f"{'Hour':<{h_col_w[0]}} {'Trades':>{h_col_w[1]}} {'WinRate':>{h_col_w[2]}} "
            f"{'AvgReturn':>{h_col_w[3]}} {'Expectancy':>{h_col_w[4]}} {'ProfitFctr':>{h_col_w[5]}}"
        )
        h_sep = "-" * sum(h_col_w + [len(h_col_w) - 1])
        print(h_sep)
        print(h_header)
        print(h_sep)
        for hour, m in sorted(h_stats.items()):
            print(
                f"{hour:02d}:00    "
                f"{m['trade_count']:>{h_col_w[1]}} "
                f"{m['win_rate']*100:>{h_col_w[2]}.1f}% "
                f"{m['avg_return']:>{h_col_w[3]}.4f} "
                f"{m['expectancy']:>{h_col_w[4]}.4f} "
                f"{m['profit_factor']:>{h_col_w[5]}.2f}"
            )
        print(h_sep)
    print()


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
