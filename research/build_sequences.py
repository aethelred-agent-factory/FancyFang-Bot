#!/usr/bin/env python3
"""Build raw OHLCV+funding-rate sequences for every trade entry.

This script is intended as the Phase 3 Step 15 pipeline described in the
roadmap. It walks the trade_history table and attempts to reconstruct a
fixed-length sequence of market data immediately preceding each entry.  The
result is written to a compressed numpy file that can be fed into an LSTM
or other sequence model.

Because the SQLite history only contains the trades themselves (no candle
archive), this implementation re-fetches recent candles for each symbol via
`core.phemex_common.get_candles`.  That means the sequences will reflect the
*current* market, not the true historical bars at the time of the trade.
For real research you should supply a local archive of historical candles
and adapt the code below to load from it.

Usage:
    python build_sequences.py --db /path/to/fancybot.db \
            --output data/sequences.npz --timeframe 1H --length 60

The output file contains the following arrays:
    X : float32 array shape (N, L, 7)  # L candles, 7 features per candle
    y : int8   array shape (N,)         # 1 = win, 0 = loss
    trade_ids : int32 array shape (N,)
    symbols : object array shape (N,)   # symbol strings

The seven features are [open, high, low, close, volume, funding_rate,
open_interest].  Open interest is currently unavailable and is filled with
zeros.  Funding rate is taken from the current market snapshot.

"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import numpy as np

# add project root to path so we can import modules
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.storage_manager import StorageManager
import core.phemex_common as pc

logger = logging.getLogger("build_sequences")
logging.basicConfig(level=logging.INFO)


def parse_args():
    parser = argparse.ArgumentParser(description="Build sequence dataset from trade history")
    parser.add_argument("--db", type=Path, required=True, help="Path to SQLite database")
    parser.add_argument("--output", type=Path, required=True, help="Output .npz file")
    parser.add_argument("--timeframe", type=str, default="1H", help="Candle timeframe to fetch")
    parser.add_argument("--length", type=int, default=60, help="Number of candles per sequence")
    return parser.parse_args()


def build_sequences(
    db_path: Path, timeframe: str = "1H", seq_len: int = 60
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (X, y, trade_ids, symbols) arrays."""
    # ensure the provided database path actually exists
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}. "
                                "Make sure you pass the correct path (e.g. data/state/fancybot.db).")
    storage = StorageManager(db_path)

    with storage._lock:
        conn = storage._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, symbol, timestamp, pnl FROM trade_history ORDER BY timestamp ASC"
        )
        rows = cursor.fetchall()
        conn.close()

    sequences: List[List[List[float]]] = []
    labels: List[int] = []
    trade_ids: List[int] = []
    symbols: List[str] = []

    for r in rows:
        tid = r[0]
        symbol = r[1]
        ts_str = r[2]
        pnl = r[3]

        try:
            entry_dt = datetime.datetime.fromisoformat(ts_str)
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=datetime.timezone.utc)
            entry_epoch = entry_dt.timestamp()
        except Exception:
            logger.warning(f"Skipping trade {tid}: bad timestamp {ts_str}")
            continue

        # fetch a generous number of candles and then pick the slice ending at entry
        candles = pc.get_candles(symbol, timeframe=timeframe, limit=500)
        if not candles:
            logger.warning(f"No candles for {symbol}, trade {tid}")
            continue

        # find last candle whose timestamp <= entry
        idx = None
        for i, c in enumerate(candles):
            try:
                bar_ts = int(c[0]) / 1000.0
            except Exception:
                continue
            if bar_ts > entry_epoch:
                idx = i - 1
                break
        if idx is None:
            idx = len(candles) - 1

        if idx < seq_len - 1:
            logger.warning(f"Not enough history for trade {tid} ({idx+1} bars)")
            continue

        slab = candles[idx - seq_len + 1 : idx + 1]
        if len(slab) != seq_len:
            logger.warning(f"Unexpected slab length for trade {tid}: {len(slab)}")
            continue

        # funding rate snapshot (current)
        fr, _, _ = pc.get_funding_rate_info(symbol)
        fr = fr or 0.0
        oi = 0.0

        arr = []
        for bar in slab:
            # bar format: [ts, interval, open, high, low, close, volume, ...]
            o = float(bar[2])
            h = float(bar[3])
            l = float(bar[4])
            c = float(bar[5])
            v = float(bar[6]) if len(bar) > 6 else 0.0
            arr.append([o, h, l, c, v, fr, oi])
        sequences.append(arr)
        labels.append(1 if pnl and pnl > 0 else 0)
        trade_ids.append(tid)
        symbols.append(symbol)

    X = np.array(sequences, dtype=np.float32)
    y = np.array(labels, dtype=np.int8)
    trade_ids_arr = np.array(trade_ids, dtype=np.int32)
    symbols_arr = np.array(symbols, dtype=object)

    return X, y, trade_ids_arr, symbols_arr


if __name__ == "__main__":
    args = parse_args()
    try:
        X, y, tids, syms = build_sequences(args.db, args.timeframe, args.length)
    except FileNotFoundError as e:
        logger.error(e)
        sys.exit(1)

    if X.size == 0:
        logger.error("No sequences generated. Exiting.")
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.output,
        X=X,
        y=y,
        trade_ids=tids,
        symbols=syms,
        timeframe=args.timeframe,
    )
    logger.info(f"Saved {len(y)} sequences (shape {X.shape}) to {args.output}")
