#!/usr/bin/env python3
import os
import sys
import logging
import time
from pathlib import Path
from typing import List, Dict, Any

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.storage_manager import StorageManager
from modules.trade_narrator import TradeNarrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("batch_annotate")

def batch_annotate(db_path: Path, limit: int = 100):
    storage = StorageManager(db_path)
    narrator = TradeNarrator()
    
    if not narrator.api_key:
        logger.error("DeepSeek API key not found. Please set DEEPSEEK_API_KEY environment variable.")
        return

    unannotated = storage.get_unannotated_trades(limit=limit)
    if not unannotated:
        logger.info("No unannotated trades found.")
        return

    logger.info(f"Found {len(unannotated)} trades to annotate.")
    
    success_count = 0
    for i, trade in enumerate(unannotated):
        symbol = trade.get("symbol")
        trade_id = trade.get("id")
        logger.info(f"[{i+1}/{len(unannotated)}] Annotating trade {trade_id} ({symbol})...")
        
        # trade record for narrator expects 'entry', 'exit', etc. 
        # get_unannotated_trades returns the full dict from DB
        
        market_ctx = trade.get("market_context", {})
        
        try:
            narration_result = narrator.narrate_closed_trade(trade, market_ctx)
            if narration_result:
                storage.update_trade_narration(trade_id, narration_result)
                success_count += 1
                logger.info(f"Successfully annotated trade {trade_id}")
            else:
                logger.warning(f"Failed to annotate trade {trade_id}")
        except Exception as e:
            logger.error(f"Error annotating trade {trade_id}: {e}")
            
        # Optional: sleep to avoid aggressive rate limiting
        # time.sleep(0.5)

    logger.info(f"Batch annotation complete. Success: {success_count}/{len(unannotated)}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Batch annotate unannotated trades in the database.")
    parser.add_argument("--limit", type=int, default=100, help="Max number of trades to annotate")
    args = parser.parse_args()
    
    SCRIPT_DIR = Path(__file__).parent
    DB_PATH = SCRIPT_DIR.parent / "data" / "state" / "backtest.db"
    
    batch_annotate(DB_PATH, limit=args.limit)
