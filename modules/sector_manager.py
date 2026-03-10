#!/usr/bin/env python3
import threading
import time
from collections import deque
from typing import Dict, List, Optional

SECTOR_MAP = {
    # AI / DePIN
    "TAOUSDT": "AI", "RENDERUSDT": "AI", "FETUSDT": "AI", "ARUSDT": "AI", "THETAUSDT": "AI",
    # MEME
    "PEPEUSDT": "MEME", "DOGEUSDT": "MEME", "SHIBUSDT": "MEME", "WIFUSDT": "MEME", "BONKUSDT": "MEME", "FLOKIUSDT": "MEME", "TRUMPUSDT": "MEME",
    # L1 / L2
    "BTCUSDT": "L1", "ETHUSDT": "L1", "SOLUSDT": "L1", "AVAXUSDT": "L1", "NEARUSDT": "L1", "DOTUSDT": "L1", "MATICUSDT": "L1", "OPUSDT": "L1", "ARBUSDT": "L1",
    # DeFi
    "AAVEUSDT": "DEFI", "UNIUSDT": "DEFI", "LINKUSDT": "DEFI", "MKRUSDT": "DEFI", "RUNEUSDT": "DEFI",
}

class SectorManager:
    """
    Tracks cross-asset momentum and sentiment across defined market sectors.
    Provides 'Cluster Alpha' signals when sectors move in unison.
    """
    def __init__(self, window_size: int = 20):
        self.sector_momentum: Dict[str, deque] = {s: deque(maxlen=window_size) for s in set(SECTOR_MAP.values())}
        self.sector_momentum["OTHER"] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def get_sector(self, symbol: str) -> str:
        return SECTOR_MAP.get(symbol, "OTHER")

    def update_momentum(self, symbol: str, score: float):
        sector = self.get_sector(symbol)
        with self._lock:
            self.sector_momentum[sector].append(score)

    def get_sector_score(self, sector: str) -> float:
        with self._lock:
            scores = self.sector_momentum.get(sector, [])
            if not scores:
                return 0.0
            return sum(scores) / len(scores)

    def get_all_sector_scores(self) -> Dict[str, float]:
        with self._lock:
            return {s: (sum(q)/len(q) if q else 0.0) for s, q in self.sector_momentum.items()}

# Singleton
sector_manager = SectorManager()
