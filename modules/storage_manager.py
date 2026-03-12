import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import datetime
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("storage_manager")


class StorageManager:
    """
    SQLite-based storage manager for FancyBot.
    Provides a clean abstraction for account state and trade history.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        # Ensure parent directory exists to avoid sqlite3.OperationalError
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                        data_json TEXT
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
                        slippage REAL,
                        raw_signals_json TEXT,
                        narrative TEXT,
                        tags_json TEXT,
                        primary_driver TEXT,
                        failure_mode TEXT,
                        market_context_json TEXT,
                        ml_features_json TEXT
                    )
                """)

                # Drawdown state table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS drawdown_state (
                        day TEXT PRIMARY KEY,
                        start_balance REAL NOT NULL,
                        daily_pnl REAL NOT NULL,
                        killed INTEGER NOT NULL,
                        kill_reason TEXT,
                        kill_count_today INTEGER NOT NULL
                    )
                """)

                # Signal stats table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS signal_stats (
                        signal_name TEXT PRIMARY KEY,
                        trade_count INTEGER NOT NULL,
                        win_count INTEGER NOT NULL,
                        loss_count INTEGER NOT NULL,
                        gross_wins REAL NOT NULL,
                        gross_losses REAL NOT NULL,
                        pnl_list_json TEXT
                    )
                """)

                # Hour stats table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS hour_stats (
                        hour INTEGER PRIMARY KEY,
                        trade_count INTEGER NOT NULL,
                        win_count INTEGER NOT NULL,
                        loss_count INTEGER NOT NULL,
                        gross_wins REAL NOT NULL,
                        gross_losses REAL NOT NULL,
                        pnl_list_json TEXT
                    )
                """)

                # Correlation matrix table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS correlation_matrix (
                        symbol1 TEXT NOT NULL,
                        symbol2 TEXT NOT NULL,
                        correlation REAL NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (symbol1, symbol2)
                    )
                """)

                # Events table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        time TEXT NOT NULL,
                        buffer_before_mins INTEGER DEFAULT 90,
                        buffer_after_mins INTEGER DEFAULT 30,
                        impact TEXT,
                        source TEXT
                    )
                """)

                # Training corpus / scan dump table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS scan_corpus (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        data_json TEXT NOT NULL
                    )
                """)

                # Ensure columns exist in trade_history (Migration)
                cursor.execute("PRAGMA table_info(trade_history)")
                cols = [c[1] for c in cursor.fetchall()]
                if "signals_json" not in cols:
                    cursor.execute(
                        "ALTER TABLE trade_history ADD COLUMN signals_json TEXT"
                    )
                if "raw_signals_json" not in cols:
                    cursor.execute(
                        "ALTER TABLE trade_history ADD COLUMN raw_signals_json TEXT"
                    )
                if "slippage" not in cols:
                    cursor.execute(
                        "ALTER TABLE trade_history ADD COLUMN slippage REAL DEFAULT 0.0"
                    )
                if "narrative" not in cols:
                    cursor.execute("ALTER TABLE trade_history ADD COLUMN narrative TEXT")
                if "tags_json" not in cols:
                    cursor.execute("ALTER TABLE trade_history ADD COLUMN tags_json TEXT")
                if "primary_driver" not in cols:
                    cursor.execute("ALTER TABLE trade_history ADD COLUMN primary_driver TEXT")
                if "failure_mode" not in cols:
                    cursor.execute("ALTER TABLE trade_history ADD COLUMN failure_mode TEXT")
                if "market_context_json" not in cols:
                    cursor.execute(
                        "ALTER TABLE trade_history ADD COLUMN market_context_json TEXT"
                    )
                if "ml_features_json" not in cols:
                    cursor.execute(
                        "ALTER TABLE trade_history ADD COLUMN ml_features_json TEXT"
                    )

                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to initialize database: {e}")
                raise
            finally:
                conn.close()

        # Initialize auxiliary ledger tables
        self._init_ledger_tables()

    def load_account(self, initial_balance: float = 100.0) -> Dict[str, Any]:
        """Loads account balance and positions."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT balance FROM account WHERE id = 1")
                row = cursor.fetchone()
                balance = float(row["balance"]) if row else initial_balance

                cursor.execute("SELECT data_json FROM positions")
                positions = [json.loads(r["data_json"]) for r in cursor.fetchall()]

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
                cursor.execute(
                    """
                    INSERT INTO account (id, balance, updated_at)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET balance=excluded.balance, updated_at=excluded.updated_at
                """,
                    (balance, now),
                )

                # Update positions
                cursor.execute("DELETE FROM positions")
                for pos in positions:
                    cursor.execute(
                        """
                        INSERT INTO positions (
                            symbol, side, size, margin, leverage, entry,
                            stop_price, take_profit, entry_time, entry_score, data_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            pos["symbol"],
                            pos["side"],
                            pos["size"],
                            pos["margin"],
                            pos.get("leverage", 0),
                            pos["entry"],
                            pos.get("stop_price"),
                            pos.get("take_profit"),
                            pos.get("entry_time", now),
                            pos.get("entry_score", 0),
                            json.dumps(pos),
                        ),
                    )
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
                cursor.execute(
                    """
                    INSERT INTO trade_history (
                        symbol, direction, entry, exit, pnl, hold_time_s,
                        score, reason, timestamp, signals_json, slippage, raw_signals_json,
                        narrative, tags_json, primary_driver, failure_mode, market_context_json,
                        ml_features_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        record.get("symbol", "UNKNOWN"),
                        record.get("direction", "UNKNOWN"),
                        record.get("entry", 0.0),
                        record.get("exit", 0.0),
                        record.get("pnl", 0.0),
                        record.get("hold_time_s"),
                        record.get("score"),
                        record.get("reason"),
                        record.get(
                            "timestamp",
                            datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        ),
                        json.dumps(record.get("signals", [])),
                        record.get("slippage", 0.0),
                        json.dumps(record.get("raw_signals", {})),
                        record.get("narrative"),
                        json.dumps(record.get("tags", [])),
                        record.get("primary_driver"),
                        record.get("failure_mode"),
                        json.dumps(record.get("market_context", {})),
                        json.dumps(record.get("ml_features", {})),
                    ),
                )
                trade_id = cursor.lastrowid
                conn.commit()
                return trade_id
            except sqlite3.Error as e:
                logger.error(f"Failed to append trade: {e}")
                return None
            finally:
                conn.close()

    def update_trade_narration(self, trade_id: int, narration_data: Dict[str, Any]):
        """Updates a trade record with narration data from the TradeNarrator."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE trade_history
                    SET
                        narrative = ?,
                        tags_json = ?,
                        primary_driver = ?,
                        failure_mode = ?
                    WHERE id = ?
                """,
                    (
                        narration_data.get("narrative"),
                        json.dumps(narration_data.get("tags", [])),
                        narration_data.get("primary_driver"),
                        narration_data.get("failure_mode"),
                        trade_id,
                    ),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to update trade narration for trade_id {trade_id}: {e}")
            finally:
                conn.close()

    def get_unannotated_trades(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Retrieves trades that haven't been narrated by DeepSeek yet."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM trade_history 
                    WHERE narrative IS NULL 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """,
                    (limit,),
                )
                rows = cursor.fetchall()
                history = []
                for r in rows:
                    item = dict(r)
                    signals_json = item.pop("signals_json", None)
                    item["signals"] = json.loads(signals_json) if signals_json else []

                    raw_sig = item.pop("raw_signals_json", None)
                    item["raw_signals"] = json.loads(raw_sig) if raw_sig else {}

                    tags_json = item.pop("tags_json", None)
                    item["tags"] = json.loads(tags_json) if tags_json else []

                    market_ctx_json = item.pop("market_context_json", None)
                    item["market_context"] = (
                        json.loads(market_ctx_json) if market_ctx_json else {}
                    )

                    ml_feat_json = item.pop("ml_features_json", None)
                    item["ml_features"] = (
                        json.loads(ml_feat_json) if ml_feat_json else {}
                    )

                    history.append(item)
                return history
            finally:
                conn.close()

    def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves recent trade history."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM trade_history ORDER BY timestamp DESC LIMIT ?
                """,
                    (limit,),
                )
                rows = cursor.fetchall()
                history = []
                for r in rows:
                    item = dict(r)
                    signals_json = item.pop("signals_json", None)
                    item["signals"] = json.loads(signals_json) if signals_json else []
                    
                    raw_sig = item.pop("raw_signals_json", None)
                    item["raw_signals"] = json.loads(raw_sig) if raw_sig else {}

                    tags_json = item.pop("tags_json", None)
                    item["tags"] = json.loads(tags_json) if tags_json else []

                    market_ctx_json = item.pop("market_context_json", None)
                    item["market_context"] = json.loads(market_ctx_json) if market_ctx_json else {}

                    ml_feat_json = item.pop("ml_features_json", None)
                    item["ml_features"] = (
                        json.loads(ml_feat_json) if ml_feat_json else {}
                    )

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

    # --- Drawdown Guard Methods ---

    def load_drawdown_state(self, day: str) -> Optional[Dict[str, Any]]:
        """Loads drawdown state for a specific day."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM drawdown_state WHERE day = ?", (day,))
                row = cursor.fetchone()
                if row:
                    res = dict(row)
                    res["killed"] = bool(res["killed"])
                    return res
                return None
            finally:
                conn.close()

    def save_drawdown_state(self, state_dict: Dict[str, Any]):
        """Saves drawdown state."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO drawdown_state (day, start_balance, daily_pnl, killed, kill_reason, kill_count_today)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(day) DO UPDATE SET
                        start_balance=excluded.start_balance,
                        daily_pnl=excluded.daily_pnl,
                        killed=excluded.killed,
                        kill_reason=excluded.kill_reason,
                        kill_count_today=excluded.kill_count_today
                """,
                    (
                        state_dict["day"],
                        state_dict["start_balance"],
                        state_dict["daily_pnl"],
                        1 if state_dict["killed"] else 0,
                        state_dict["kill_reason"],
                        state_dict["kill_count_today"],
                    ),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to save drawdown state: {e}")
            finally:
                conn.close()

    # --- Signal Analytics Methods ---

    def load_signal_stats(self) -> Dict[str, Any]:
        """Loads all signal statistics."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM signal_stats")
                rows = cursor.fetchall()
                stats = {}
                for r in rows:
                    name = r["signal_name"]
                    stats[name] = {
                        "trade_count": r["trade_count"],
                        "win_count": r["win_count"],
                        "loss_count": r["loss_count"],
                        "gross_wins": r["gross_wins"],
                        "gross_losses": r["gross_losses"],
                        "pnl_list": json.loads(r["pnl_list_json"]),
                    }
                return stats
            finally:
                conn.close()

    def save_signal_stats(self, stats: Dict[str, Dict[str, Any]]):
        """Saves all signal statistics."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                for name, b in stats.items():
                    cursor.execute(
                        """
                        INSERT INTO signal_stats (
                            signal_name, trade_count, win_count, loss_count,
                            gross_wins, gross_losses, pnl_list_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(signal_name) DO UPDATE SET
                            trade_count=excluded.trade_count,
                            win_count=excluded.win_count,
                            loss_count=excluded.loss_count,
                            gross_wins=excluded.gross_wins,
                            gross_losses=excluded.gross_losses,
                            pnl_list_json=excluded.pnl_list_json
                    """,
                        (
                            name,
                            b["trade_count"],
                            b["win_count"],
                            b["loss_count"],
                            b["gross_wins"],
                            b["gross_losses"],
                            json.dumps(b["pnl_list"]),
                        ),
                    )
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to save signal stats: {e}")
            finally:
                conn.close()

    def load_hour_stats(self) -> Dict[int, Any]:
        """Loads all hour statistics."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM hour_stats")
                rows = cursor.fetchall()
                stats = {}
                for r in rows:
                    hour = r["hour"]
                    stats[hour] = {
                        "trade_count": r["trade_count"],
                        "win_count": r["win_count"],
                        "loss_count": r["loss_count"],
                        "gross_wins": r["gross_wins"],
                        "gross_losses": r["gross_losses"],
                        "pnl_list": json.loads(r["pnl_list_json"]),
                    }
                return stats
            finally:
                conn.close()

    def save_hour_stats(self, stats: Dict[int, Dict[str, Any]]):
        """Saves all hour statistics."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                for hour, b in stats.items():
                    cursor.execute(
                        """
                        INSERT INTO hour_stats (
                            hour, trade_count, win_count, loss_count,
                            gross_wins, gross_losses, pnl_list_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(hour) DO UPDATE SET
                            trade_count=excluded.trade_count,
                            win_count=excluded.win_count,
                            loss_count=excluded.loss_count,
                            gross_wins=excluded.gross_wins,
                            gross_losses=excluded.gross_losses,
                            pnl_list_json=excluded.pnl_list_json
                    """,
                        (
                            hour,
                            b["trade_count"],
                            b["win_count"],
                            b["loss_count"],
                            b["gross_wins"],
                            b["gross_losses"],
                            json.dumps(b["pnl_list"]),
                        ),
                    )
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to save hour stats: {e}")
            finally:
                conn.close()

    # --- Model Training State API -----------------------------------------

    def get_model_training_state(self) -> Dict[str, Any]:
        """Returns the current model training tracking state."""
        self._init_ledger_tables()
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT last_training_timestamp, trades_since_training FROM model_training_state WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    return {
                        "last_training_timestamp": row["last_training_timestamp"],
                        "trades_since_training": row["trades_since_training"],
                    }
                return {"last_training_timestamp": None, "trades_since_training": 0}
            finally:
                conn.close()

    def count_annotated_trades(self) -> int:
        """Counts total number of annotated trades (narrative IS NOT NULL)."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM trade_history WHERE narrative IS NOT NULL")
                return cursor.fetchone()[0]
            finally:
                conn.close()

    def increment_trades_since_training(self, n: int = 1):
        """Increments the counter of trades since last model training."""
        self._init_ledger_tables()
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE model_training_state SET trades_since_training = trades_since_training + ? WHERE id = 1", (n,))
                conn.commit()
            finally:
                conn.close()

    def reset_model_training_state(self):
        """Resets the trade counter and updates the last training timestamp."""
        self._init_ledger_tables()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE model_training_state SET trades_since_training = 0, last_training_timestamp = ? WHERE id = 1",
                    (now,)
                )
                conn.commit()
            finally:
                conn.close()

    def update_last_training_timestamp(self, timestamp: str):
        """Record when the models were last retrained."""
        self._init_ledger_tables()
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE model_training_state SET last_training_timestamp = ? WHERE id = 1",
                    (timestamp,),
                )
                conn.commit()
            finally:
                conn.close()

    # Correlation Matrix API -----------------------------------------------

    def save_correlation_matrix(self, matrix: Dict[str, Dict[str, float]]):
        """Saves a symmetric correlation matrix to the database."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor.execute("DELETE FROM correlation_matrix")
                for s1, row in matrix.items():
                    for s2, corr in row.items():
                        cursor.execute(
                            """
                            INSERT INTO correlation_matrix (symbol1, symbol2, correlation, updated_at)
                            VALUES (?, ?, ?, ?)
                        """,
                            (s1, s2, corr, now),
                        )
                conn.commit()
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to save correlation matrix: {e}")
            finally:
                conn.close()

    def load_correlation_matrix(
        self,
    ) -> Tuple[Dict[str, Dict[str, float]], Optional[str]]:
        """Loads the most recent correlation matrix and its update timestamp."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM correlation_matrix")
                rows = cursor.fetchall()
                matrix = {}
                updated_at = None
                for r in rows:
                    s1, s2, corr = r["symbol1"], r["symbol2"], r["correlation"]
                    updated_at = r["updated_at"]
                    if s1 not in matrix:
                        matrix[s1] = {}
                    matrix[s1][s2] = corr
                return matrix, updated_at
            finally:
                conn.close()

    # Events API -----------------------------------------------------------

    def save_events(self, events: List[Dict[str, Any]]):
        """Saves events to the database (replacing old ones)."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM events")
                for e in events:
                    cursor.execute(
                        """
                        INSERT INTO events (name, time, buffer_before_mins, buffer_after_mins, impact, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            e.get("name", "Unknown"),
                            e.get("time"),
                            e.get("buffer_before", 90),
                            e.get("buffer_after", 30),
                            e.get("impact"),
                            e.get("source"),
                        ),
                    )
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to save events: {e}")
            finally:
                conn.close()

    def get_upcoming_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves upcoming events."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor.execute(
                    """
                    SELECT * FROM events WHERE time > ? ORDER BY time ASC LIMIT ?
                """,
                    (now, limit),
                )
                rows = cursor.fetchall()
                events = []
                for r in rows:
                    events.append(
                        {
                            "name": r["name"],
                            "time": r["time"],
                            "buffer_before": r["buffer_before_mins"],
                            "buffer_after": r["buffer_after_mins"],
                            "impact": r["impact"],
                            "source": r["source"],
                        }
                    )
                return events
            finally:
                conn.close()

    # --- Auxiliary Ledger Tables ---

    def _init_ledger_tables(self):
        """Ensures auxiliary ledger tables exist (blacklist, system_config, sim_state, training_state)."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()

                # Symbol blacklist table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS blacklist (
                        symbol TEXT PRIMARY KEY,
                        reason TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT
                    )
                """)

                # System configuration regimes
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS system_config (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        params_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 0
                    )
                """)

                # Serialized bot state snapshots
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sim_state (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        is_current INTEGER NOT NULL DEFAULT 0
                    )
                """)

                # Model training state
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS model_training_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        last_training_timestamp TEXT,
                        trades_since_training INTEGER DEFAULT 0
                    )
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO model_training_state (id, last_training_timestamp, trades_since_training)
                    VALUES (1, NULL, 0)
                """)

                # Training Corpus table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS training_corpus (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        features_json TEXT,
                        sequence_json TEXT,
                        score REAL,
                        narrative TEXT,
                        outcome_pnl REAL,
                        market_context_json TEXT
                    )
                """)

                conn.commit()
            finally:
                conn.close()

    # Training Corpus API --------------------------------------------------

    def append_to_corpus(self, record: Dict[str, Any]):
        """Appends a scan result or trade outcome to the training corpus."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO training_corpus (
                        timestamp, symbol, direction, features_json, sequence_json,
                        score, narrative, outcome_pnl, market_context_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        record.get("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat()),
                        record.get("symbol"),
                        record.get("direction"),
                        json.dumps(record.get("features", {})),
                        json.dumps(record.get("sequence", [])),
                        record.get("score"),
                        record.get("narrative"),
                        record.get("outcome_pnl"),
                        json.dumps(record.get("market_context", {})),
                    ),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to append to corpus: {e}")
            finally:
                conn.close()

    # Blacklist API --------------------------------------------------------

    def add_to_blacklist(
        self, symbol: str, reason: str, expires_at: Optional[datetime.datetime] = None
    ):
        """Adds or updates a blacklisted symbol with an optional expiry."""
        self._init_ledger_tables()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        expiry_str = expires_at.isoformat() if expires_at else None
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO blacklist (symbol, reason, created_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        reason=excluded.reason,
                        created_at=excluded.created_at,
                        expires_at=excluded.expires_at
                    """,
                    (symbol, reason, now, expiry_str),
                )
                conn.commit()
            finally:
                conn.close()

    def remove_from_blacklist(self, symbol: str):
        """Removes a symbol from the blacklist."""
        self._init_ledger_tables()
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM blacklist WHERE symbol = ?", (symbol,))
                conn.commit()
            finally:
                conn.close()

    def get_blacklist(self) -> Dict[str, Dict[str, Any]]:
        """Returns all currently active blacklist entries as a dict keyed by symbol."""
        self._init_ledger_tables()
        now = datetime.datetime.now(datetime.timezone.utc)
        active: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT symbol, reason, created_at, expires_at FROM blacklist"
                )
                rows = cursor.fetchall()
                for r in rows:
                    exp = r["expires_at"]
                    if exp:
                        try:
                            exp_dt = datetime.datetime.fromisoformat(exp)
                        except Exception:
                            exp_dt = None
                        if exp_dt and exp_dt <= now:
                            continue
                    active[r["symbol"]] = {
                        "reason": r["reason"],
                        "created_at": r["created_at"],
                        "expires_at": r["expires_at"],
                    }
            finally:
                conn.close()
        return active

    def is_blacklisted(self, symbol: str) -> bool:
        """Returns True if symbol is currently active on the blacklist."""
        return symbol in self.get_blacklist()

    # System configuration API ---------------------------------------------

    def set_system_config(
        self, name: str, params: Dict[str, Any], activate: bool = False
    ):
        """
        Upserts a named configuration regime.
        When activate=True, marks this config as the single active regime.
        """
        self._init_ledger_tables()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        params_json = json.dumps(params)
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO system_config (name, params_json, created_at, is_active)
                    VALUES (?, ?, ?, COALESCE(?, 0))
                    ON CONFLICT(name) DO UPDATE SET
                        params_json=excluded.params_json,
                        created_at=excluded.created_at
                    """,
                    (name, params_json, now, 1 if activate else 0),
                )
                if activate:
                    cursor.execute(
                        "UPDATE system_config SET is_active = CASE WHEN name = ? THEN 1 ELSE 0 END",
                        (name,),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_system_config(self, name: str) -> Optional[Dict[str, Any]]:
        """Returns a specific named configuration regime, or None if missing."""
        self._init_ledger_tables()
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name, params_json, created_at, is_active FROM system_config WHERE name = ?",
                    (name,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "name": row["name"],
                    "params": json.loads(row["params_json"]),
                    "created_at": row["created_at"],
                    "is_active": bool(row["is_active"]),
                }
            finally:
                conn.close()

    def get_active_config(self) -> Optional[Dict[str, Any]]:
        """Returns the currently active configuration regime, if any."""
        self._init_ledger_tables()
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name, params_json, created_at, is_active FROM system_config WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "name": row["name"],
                    "params": json.loads(row["params_json"]),
                    "created_at": row["created_at"],
                    "is_active": bool(row["is_active"]),
                }
            finally:
                conn.close()

    # Sim state snapshot API -----------------------------------------------

    def save_sim_state(self, snapshot: Dict[str, Any]):
        """
        Persists a full simulation bot snapshot.
        """
        self._init_ledger_tables()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = json.dumps(snapshot)
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE sim_state SET is_current = 0 WHERE is_current = 1"
                )
                cursor.execute(
                    """
                    INSERT INTO sim_state (snapshot_json, created_at, is_current)
                    VALUES (?, ?, 1)
                    """,
                    (payload, now),
                )
                conn.commit()
            finally:
                conn.close()

    def load_latest_sim_state(self) -> Optional[Dict[str, Any]]:
        """Loads the most recent sim_state snapshot, if any."""
        self._init_ledger_tables()
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT snapshot_json FROM sim_state WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return json.loads(row["snapshot_json"])
            finally:
                conn.close()

    def get_upcoming_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves upcoming events."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor.execute(
                    """
                    SELECT * FROM events WHERE time > ? ORDER BY time ASC LIMIT ?
                """,
                    (now, limit),
                )
                rows = cursor.fetchall()
                events = []
                for r in rows:
                    events.append(
                        {
                            "name": r["name"],
                            "time": r["time"],
                            "buffer_before": r["buffer_before_mins"],
                            "buffer_after": r["buffer_after_mins"],
                            "impact": r["impact"],
                            "source": r["source"],
                        }
                    )
                return events
            finally:
                conn.close()

    # -------------------------------------------------------------------------
    # VoltAgent helper APIs (Appendix A)
    # -------------------------------------------------------------------------

    def get_trade_by_id(self, trade_id: int) -> Optional[Dict[str, Any]]:
        """Return a single trade record by its ID, with JSON fields decoded."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM trade_history WHERE id = ?", (trade_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                item = dict(row)
                # decode JSON fields
                for field in [
                    "signals_json",
                    "raw_signals_json",
                    "tags_json",
                    "market_context_json",
                    "ml_features_json",
                ]:
                    if item.get(field) is not None:
                        try:
                            item[field.replace("_json", "")] = json.loads(item[field])
                        except Exception:
                            item[field.replace("_json", "")] = None
                    else:
                        item[field.replace("_json", "")] = None
                    item.pop(field, None)
                return item
            finally:
                conn.close()

    def get_failure_mode_distribution(self) -> Dict[str, int]:
        """Return counts of each failure mode observed in trade_history.

        Used by the FailureGuardAgent to understand historical frequencies.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT failure_mode, COUNT(*) as cnt FROM trade_history GROUP BY failure_mode"
                )
                rows = cursor.fetchall()
                dist: Dict[str, int] = {}
                for r in rows:
                    mode = r["failure_mode"] or "NONE"
                    dist[mode] = r["cnt"]
                return dist
            finally:
                conn.close()

    def get_latest_features(self, symbol: str) -> Dict[str, Any]:
        """Fetch the most recent ml_features for a given symbol.

        VoltAgent uses this to inspect a candidate's feature vector.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT ml_features_json FROM trade_history
                    WHERE symbol = ? AND ml_features_json IS NOT NULL
                    ORDER BY timestamp DESC LIMIT 1
                """,
                    (symbol,)
                )
                row = cursor.fetchone()
                if not row or not row["ml_features_json"]:
                    return {}
                try:
                    return json.loads(row["ml_features_json"])
                except Exception:
                    return {}
            finally:
                conn.close()
