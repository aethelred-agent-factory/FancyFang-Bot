#!/usr/bin/env python3
import os
import sys
import json
import csv
import logging
from pathlib import Path
from typing import List, Dict, Any

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.storage_manager import StorageManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("export_training_data")

def export_data(db_path: Path, output_csv: Path):
    storage = StorageManager(db_path)
    
    # Fetch all annotated trades
    # We'll use a larger limit to get as much data as possible
    with storage._lock:
        conn = storage._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trade_history 
                WHERE narrative IS NOT NULL 
                ORDER BY timestamp ASC
            """)
            rows = cursor.fetchall()
        finally:
            conn.close()

    if not rows:
        logger.warning("No annotated trades found in database.")
        return

    logger.info(f"Exporting {len(rows)} annotated trades...")

    # Define headers
    # We want features from raw_signals_json and market_context_json
    # Plus outcome labels
    
    all_data = []
    feature_keys = set()
    
    for row in rows:
        item = dict(row)
        
        # Parse JSON fields
        raw_signals = json.loads(item.get("raw_signals_json", "{}"))
        market_ctx = json.loads(item.get("market_context_json", "{}"))
        tags = json.loads(item.get("tags_json", "[]"))
        
        # Flatten features
        entry = {}
        entry["trade_id"] = item["id"]
        entry["symbol"] = item["symbol"]
        entry["direction"] = item["direction"]
        entry["timestamp"] = item["timestamp"]
        
        # Outcome labels
        entry["pnl"] = item["pnl"]
        entry["is_win"] = 1 if item["pnl"] > 0 else 0
        entry["hold_time_s"] = item["hold_time_s"]
        
        # ML Labels from DeepSeek
        entry["primary_driver"] = item["primary_driver"]
        entry["failure_mode"] = item["failure_mode"]
        
        # Signals
        for k, v in raw_signals.items():
            if isinstance(v, (int, float)):
                key = f"sig_{k}"
                entry[key] = v
                feature_keys.add(key)
        
        # Market Context
        for k, v in market_ctx.items():
            if isinstance(v, (int, float)):
                key = f"ctx_{k}"
                entry[key] = v
                feature_keys.add(key)
        
        # One-hot encode tags (optional, but requested in Step 10)
        # We'll handle this during training or here
        # For simplicity in CSV, we'll just store tags as a string
        entry["tags"] = "|".join(tags)
        
        all_data.append(entry)

    # Write to CSV
    sorted_features = sorted(list(feature_keys))
    headers = ["trade_id", "symbol", "direction", "timestamp", "is_win", "pnl", "hold_time_s", "primary_driver", "failure_mode", "tags"] + sorted_features
    
    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for entry in all_data:
            # Fill missing features with 0.0
            row_to_write = {k: entry.get(k, 0.0) for k in headers}
            writer.writerow(row_to_write)

    logger.info(f"Successfully exported data to {output_csv}")

if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).parent
    DB_PATH = SCRIPT_DIR.parent / "data" / "state" / "backtest.db"
    OUTPUT_CSV = SCRIPT_DIR.parent / "data" / "training_data.csv"
    
    export_data(DB_PATH, OUTPUT_CSV)
