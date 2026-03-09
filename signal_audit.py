#!/usr/bin/env python3
"""
Signal Orthogonality Audit — Upgrade #16
=========================================
Analyzes trade history to identify redundant technical indicators.
Computes a Pearson correlation matrix of raw sub-signal values using pandas.
"""

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from storage_manager import StorageManager

# Paths
SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "fancybot.db"

def run_audit(limit: int = 500):
    print(f"--- Signal Orthogonality Audit (Last {limit} trades) ---")
    
    storage = StorageManager(DB_PATH)
    history = storage.get_trade_history(limit=limit)
    
    if not history:
        print("No trade history found in database.")
        return

    # 1. Extract raw signals
    all_keys = set()
    rows = []
    for trade in history:
        raw_sig = trade.get("raw_signals", {})
        if not raw_sig:
            continue
        all_keys.update(raw_sig.keys())
        rows.append(raw_sig)

    if not rows:
        print("No raw signal data found in trade history. (New feature, needs fresh trades)")
        return

    sorted_keys = sorted(list(all_keys))
    
    # 2. Build data arrays
    valid_indices = [True] * len(rows)
    
    # First pass: find rows where all signals are present and not None
    for i, row in enumerate(rows):
        for k in sorted_keys:
            if k not in row or row[k] is None:
                valid_indices[i] = False
                break
    
    filtered_rows = [rows[i] for i, valid in enumerate(valid_indices) if valid]
    if len(filtered_rows) < 5:
        print(f"Insufficient complete data points (found {len(filtered_rows)}, need at least 5).")
        return

    # 3. Compute Correlation Matrix using Pandas
    print(f"\nAnalyzing {len(filtered_rows)} complete data points for {len(sorted_keys)} indicators.")
    
    df = pd.DataFrame(filtered_rows)[sorted_keys]
    corr_matrix = df.corr()

    # 4. Print Matrix
    print("\nPairwise Pearson Correlation Matrix:")
    print(corr_matrix.round(2))

    # 5. Identify Redundancies
    print("\nHigh Redundancy Alerts (> 0.70 or < -0.70):")
    # Use stack to easily find pairs
    pairs = corr_matrix.stack().reset_index()
    pairs.columns = ['Signal 1', 'Signal 2', 'Correlation']
    # Filter for unique pairs with high correlation
    high_corr = pairs[(pairs['Signal 1'] < pairs['Signal 2']) & (pairs['Correlation'].abs() > 0.70)]
    
    if high_corr.empty:
        print("No high redundancies detected. Your signals are well-orthogonalized!")
    else:
        for _, row in high_corr.sort_values('Correlation', key=abs, ascending=False).iterrows():
            print(f"  [!] {row['Signal 1']:<15} <-> {row['Signal 2']:<15} | Correlation: {row['Correlation']:+.2f}")
        
        print("\nRecommendations:")
        print(" - For high positive correlation: Consider removing one or combining them into a single weight.")
        print(" - For high negative correlation: They may be measuring opposite sides of the same phenomenon.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FancyBot Signal Orthogonality Audit")
    parser.add_argument("--limit", type=int, default=500, help="Number of recent trades to analyze")
    args = parser.parse_args()
    
    run_audit(limit=args.limit)
