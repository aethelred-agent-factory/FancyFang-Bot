#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
"""
Correlation Manager — Upgrade #15
==================================
Tracks pairwise correlations between tradeable instruments.
Prevents over-exposure to highly correlated assets in the same direction.
"""

import datetime
import logging
import math
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import core.phemex_common as pc
from modules.storage_manager import StorageManager

logger = logging.getLogger("correlation_mgr")

# Config
CORRELATION_THRESHOLD = float(os.getenv("CORRELATION_THRESHOLD", "0.75"))
CORRELATION_LOOKBACK_DAYS = int(os.getenv("CORRELATION_LOOKBACK_DAYS", "30"))

class CorrelationManager:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self.matrix: Dict[str, Dict[str, float]] = {}
        self.updated_at: Optional[str] = None
        self._lock = threading.RLock()
        self.load()

    def load(self):
        """Loads matrix from database."""
        with self._lock:
            self.matrix, self.updated_at = self.storage.load_correlation_matrix()
            if self.matrix:
                logger.info(f"Correlation matrix loaded (updated at {self.updated_at})")

    def save(self):
        """Saves matrix to database."""
        with self._lock:
            self.storage.save_correlation_matrix(self.matrix)

    def update_matrix(self, symbols: List[str], rps: float = 10.0):
        """
        Fetches historical data for symbols and recomputes the correlation matrix.
        Should be run weekly.
        """
        logger.info(f"Updating correlation matrix for {len(symbols)} symbols...")
        
        # 1. Fetch historical daily closes for each symbol
        data: Dict[str, List[float]] = {}
        limit = CORRELATION_LOOKBACK_DAYS
        
        for symbol in symbols:
            try:
                # Use 1D timeframe for stability
                candles = pc.get_candles(symbol, timeframe="1D", limit=limit, rps=rps)
                if candles and len(candles) >= limit * 0.8:
                    closes = [float(c[6]) for c in candles]
                    data[symbol] = closes
                else:
                    logger.debug(f"Insufficient daily data for {symbol}")
            except Exception as e:
                logger.error(f"Failed to fetch daily data for {symbol}: {e}")

        # 2. Compute pairwise correlations
        valid_symbols = list(data.keys())
        new_matrix: Dict[str, Dict[str, float]] = {s: {} for s in valid_symbols}
        
        for i, s1 in enumerate(valid_symbols):
            new_matrix[s1][s1] = 1.0
            for j in range(i + 1, len(valid_symbols)):
                s2 = valid_symbols[j]
                
                # Align data lengths (should already be aligned by Phemex API, but just in case)
                len1, len2 = len(data[s1]), len(data[s2])
                min_len = min(len1, len2)
                d1 = data[s1][-min_len:]
                d2 = data[s2][-min_len:]
                
                # Pearson correlation
                try:
                    corr = np.corrcoef(d1, d2)[0, 1]
                    if not math.isnan(corr):
                        new_matrix[s1][s2] = float(corr)
                        new_matrix[s2][s1] = float(corr)
                except Exception:
                    pass

        with self._lock:
            self.matrix = new_matrix
            self.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.save()
        
        logger.info(f"Correlation matrix updated for {len(valid_symbols)} symbols.")

    def get_correlation(self, s1: str, s2: str) -> float:
        """Returns the correlation between two symbols (default 0.0 if unknown)."""
        with self._lock:
            return self.matrix.get(s1, {}).get(s2, 0.0)

    def should_block_entry(
        self,
        candidate_symbol: str,
        direction: str,
        open_positions: List[Dict[str, Any]],
        threshold: float = CORRELATION_THRESHOLD
    ) -> Tuple[bool, str]:
        """
        Returns (True, reason) if entering candidate_symbol would exceed correlation threshold
        with any existing position in the SAME direction.
        """
        # Direction-aware: only block if we are already exposed in the same direction
        # (e.g., long BTC and trying to long highly-correlated ETH).
        
        target_side = "Buy" if direction == "LONG" else "Sell"
        
        with self._lock:
            for pos in open_positions:
                pos_symbol = pos.get("symbol")
                pos_side = pos.get("side")
                
                if pos_symbol == candidate_symbol:
                    continue # already handled by scanner/bot re-entry logic
                
                if pos_side == target_side:
                    corr = self.get_correlation(candidate_symbol, pos_symbol)
                    if corr > threshold:
                        dir_str = "Long" if direction == "LONG" else "Short"
                        return True, f"High correlation ({corr:.2f}) with existing {dir_str} on {pos_symbol}"
        
        return False, ""

# Global instance will be initialized by the bots
correlation_mgr: Optional[CorrelationManager] = None

def init(storage: StorageManager):
    global correlation_mgr
    correlation_mgr = CorrelationManager(storage)
