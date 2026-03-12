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

logger = logging.getLogger("event_filter")

# Paths
STATE_FILE = Path(__file__).parent / "event_filter_state.json"

# Config
DEFAULT_BUFFER_BEFORE_MINS = 90
DEFAULT_BUFFER_AFTER_MINS = 30


class EventFilter:
    def __init__(self, storage: Optional[StorageManager] = None):
        self.manual_until: Optional[datetime.datetime] = None
        self.events: List[Dict[str, Any]] = []
        self.storage = storage
        self._lock = threading.RLock()
        self.load_state()
        self.refresh_events()

    def load_state(self):
        with self._lock:
            if STATE_FILE.exists():
                try:
                    data = json.loads(STATE_FILE.read_text())
                    manual_ts = data.get("manual_until")
                    if manual_ts:
                        self.manual_until = datetime.datetime.fromisoformat(manual_ts)
                        if self.manual_until.tzinfo is None:
                            self.manual_until = self.manual_until.replace(
                                tzinfo=datetime.timezone.utc
                            )
                except Exception as e:
                    logger.error(f"Failed to load event filter state: {e}")

    def save_state(self):
        with self._lock:
            data = {
                "manual_until": (
                    self.manual_until.isoformat() if self.manual_until else None
                )
            }
            try:
                STATE_FILE.write_text(json.dumps(data))
            except Exception as e:
                logger.error(f"Failed to save event filter state: {e}")

    def refresh_events(self):
        """Reloads events from storage or placeholder."""
        with self._lock:
            if self.storage:
                # 1. Try to load from database first
                db_events = self.storage.get_upcoming_events(limit=50)
                if db_events:
                    self.events = db_events
                    return

            # 2. Fallback to local list if database is empty
            self.events = []

    def fetch_events_api(self, api_key: Optional[str] = None):
        """
        Fetches high-impact economic events from an API (e.g., Finnhub).
        [T3-01] Added placeholder for automated event fetching as per the 4 Gaps instructions.
        """
        if not api_key:
            logger.warning("No API key provided for event fetching. Skipping.")
            return

        try:
            # Example for Finnhub:
            # url = f"https://finnhub.io/api/v1/calendar/economic?token={api_key}"
            # r = requests.get(url, timeout=10)
            # data = r.json()
            # ... process and save to storage ...
            pass
        except Exception as e:
            logger.error(f"Failed to fetch events from API: {e}")

    def block_manual(self, minutes: int):
        """Manually block entries for the next N minutes."""
        with self._lock:
            now = datetime.datetime.now(datetime.timezone.utc)
            self.manual_until = now + datetime.timedelta(minutes=minutes)
            self.save_state()
            logger.info(
                f"Manual suppression activated for {minutes} minutes (until {self.manual_until})"
            )

    def unblock(self):
        """Clears manual suppression."""
        with self._lock:
            self.manual_until = None
            self.save_state()
            logger.info("Manual suppression cleared.")

    def should_suppress(self) -> Tuple[bool, str]:
        """Returns (True, reason) if trading should be suppressed."""
        now = datetime.datetime.now(datetime.timezone.utc)

        with self._lock:
            # 1. Check manual suppression
            if self.manual_until and now < self.manual_until:
                remaining = int((self.manual_until - now).total_seconds() / 60)
                return True, f"Manual block active ({remaining}m remaining)"

            # 2. Check scheduled events
            for event in self.events:
                try:
                    event_time = datetime.datetime.fromisoformat(event["time"])
                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=datetime.timezone.utc)

                    buffer_before = datetime.timedelta(
                        minutes=event.get("buffer_before", DEFAULT_BUFFER_BEFORE_MINS)
                    )
                    buffer_after = datetime.timedelta(
                        minutes=event.get("buffer_after", DEFAULT_BUFFER_AFTER_MINS)
                    )

                    start_block = event_time - buffer_before
                    end_block = event_time + buffer_after

                    if start_block <= now <= end_block:
                        name = event.get("name", "Unnamed Event")
                        return True, f"Event suppression: {name} at {event['time']}"
                except Exception as e:
                    logger.error(f"Error checking event {event}: {e}")

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
