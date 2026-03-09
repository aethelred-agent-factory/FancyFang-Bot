import sqlite3
import json
import logging
import datetime
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("storage_manager")

class StorageManager:
    """
    SQLite-based storage manager for FancyBot.
    Provides a clean abstraction for account state and trade history.
    """
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self):
        """Returns a thread-local SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes the database schema."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # Account table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS account (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        balance REAL NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)

                # Positions table (JSON blob for simplicity or structured)
                # Let's go structured for better queryability
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS positions (
                        symbol TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        size REAL NOT NULL,
                        margin REAL NOT NULL,
                        leverage INTEGER NOT NULL,
                        entry REAL NOT NULL,
                        stop_price REAL,
                        take_profit REAL,
                        entry_time TEXT NOT NULL,
                        entry_score REAL,
                        data_json TEXT -- Full raw position data for compatibility
                    )
                """)

                # Trades (History) table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trade_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        entry REAL NOT NULL,
                        exit REAL NOT NULL,
                        pnl REAL NOT NULL,
                        hold_time_s INTEGER,
                        score REAL,
                        reason TEXT,
                        timestamp TEXT NOT NULL,
                        signals_json TEXT,
                        slippage REAL
                    )
                """)
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to initialize database: {e}")
                raise
            finally:
                conn.close()

    def load_account(self, initial_balance: float = 100.0) -> Dict[str, Any]:
        """Loads account balance and positions."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT balance FROM account WHERE id = 1")
                row = cursor.fetchone()
                balance = float(row['balance']) if row else initial_balance

                cursor.execute("SELECT data_json FROM positions")
                positions = [json.loads(r['data_json']) for r in cursor.fetchall()]

                return {"balance": balance, "positions": positions}
            finally:
                conn.close()

    def save_account_state(self, balance: float, positions: List[Dict[str, Any]]):
        """Saves both balance and current positions atomically."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()

                # Update balance
                cursor.execute("""
                    INSERT INTO account (id, balance, updated_at)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET balance=excluded.balance, updated_at=excluded.updated_at
                """, (balance, now))

                # Update positions
                cursor.execute("DELETE FROM positions")
                for pos in positions:
                    cursor.execute("""
                        INSERT INTO positions (
                            symbol, side, size, margin, leverage, entry,
                            stop_price, take_profit, entry_time, entry_score, data_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        pos['symbol'], pos['side'], pos['size'], pos['margin'],
                        pos.get('leverage', 0), pos['entry'],
                        pos.get('stop_price'), pos.get('take_profit'),
                        pos.get('entry_time', now), pos.get('entry_score', 0),
                        json.dumps(pos)
                    ))
                conn.commit()
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to save account state: {e}")
            finally:
                conn.close()

    def append_trade(self, record: Dict[str, Any]):
        """Appends a closed trade to the history table."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO trade_history (
                        symbol, direction, entry, exit, pnl, hold_time_s,
                        score, reason, timestamp, signals_json, slippage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record['symbol'], record['direction'], record['entry'],
                    record['exit'], record['pnl'], record.get('hold_time_s'),
                    record.get('score'), record.get('reason'),
                    record['timestamp'], json.dumps(record.get('signals', [])),
                    record.get('slippage', 0.0)
                ))
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to append trade: {e}")
            finally:
                conn.close()

    def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves recent trade history."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM trade_history ORDER BY timestamp DESC LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                history = []
                for r in rows:
                    item = dict(r)
                    item['signals'] = json.loads(item.pop('signals_json'))
                    history.append(item)
                return history
            finally:
                conn.close()

    def clear_positions(self):
        """Removes all open positions from storage."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM positions")
                conn.commit()
            finally:
                conn.close()
