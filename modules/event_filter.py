#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
"""
Event Filter — Upgrade #14
============================
Prevents trade entries during high-impact economic events.
Supports manual blocking via Telegram and a local event list.
"""

import datetime
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.storage_manager import StorageManager
from modules.trade_narrator import TradeNarrator
from modules.market_context import market_ctx_manager

logger = logging.getLogger("event_filter")

class EventFilter:
    def __init__(self, storage: Optional[StorageManager] = None):
        self.narrator = TradeNarrator()
        self.storage = storage
        self._lock = threading.RLock()

    def should_suppress(self, setup: Dict[str, Any] = {}) -> Tuple[bool, str]:
        """
        Returns (True, reason) if trading should be suppressed based on news.
        This now delegates to the TradeNarrator.
        """
        try:
            # The market context manager already caches headlines.
            headlines = market_ctx_manager.fetch_cryptopanic_important()
            if not headlines:
                return False, ""

            suppress, reason = self.narrator.should_suppress_entry(headlines, setup)
            return suppress, reason
        except Exception as e:
            logger.error(f"Error during news suppression check: {e}")
            return False, ""

    def get_status(self) -> str:
        suppressed, reason = self.should_suppress()
        if suppressed:
            return f"🔴 Suppressed: {reason}"
        return "🟢 Active (No news/events)"


# Global instance (initially empty, set by init())
filter: EventFilter = None  # type: ignore


def init(storage: StorageManager):
    global filter
    filter = EventFilter(storage)

