#!/usr/bin/env python3
import json
from collections import defaultdict
from typing import Dict, Any, List

def analyze_results(filepath: str) -> Dict[str, Any]:
    """
    Analyzes backtest_results.json and returns statistics for symbols and signals.
    Expected format for web UI:
    {
        "symbols": [{"symbol": "BTC", "trades": 10, "pnl": 1.5, "win_rate": 60.0}, ...],
        "signals": [{"signal": "RSI", "trades": 20, "pnl": 3.0, "win_rate": 55.0}, ...],
        "total_trades": 100,
        "total_pnl": 15.5
    }
    """
    try:
        with open(filepath, 'r') as f:
            trades = json.load(f)
    except Exception as e:
        return {"error": str(e), "symbols": [], "signals": [], "total_trades": 0, "total_pnl": 0}

    symbol_map = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    signal_map = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    total_pnl = 0.0
    total_trades = 0

    for trade in trades:
        sym = trade.get('symbol')
        pnl = trade.get('pnl_usdt')
        if sym is None or pnl is None:
            continue

        total_trades += 1
        total_pnl += pnl

        symbol_map[sym]["pnl"] += pnl
        symbol_map[sym]["trades"] += 1
        if pnl > 0:
            symbol_map[sym]["wins"] += 1

        for signal in trade.get('signals', []):
            # Clean up signal string (remove values in parentheses, e.g. "RSI (70.1)" -> "RSI")
            sig_name = signal.split('(')[0].strip()
            # Also handle key:value style if present
            if ':' in sig_name and not sig_name.startswith('PREDICTIVE'):
                sig_name = sig_name.split(':')[0].strip()
            
            signal_map[sig_name]["pnl"] += pnl
            signal_map[sig_name]["trades"] += 1
            if pnl > 0:
                signal_map[sig_name]["wins"] += 1

    # Format for frontend
    symbols_list = []
    for sym, stats in symbol_map.items():
        wr = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        symbols_list.append({
            "symbol": sym,
            "trades": stats['trades'],
            "pnl": stats['pnl'],
            "win_rate": wr
        })
    
    # Sort symbols by PnL descending
    symbols_list.sort(key=lambda x: x['pnl'], reverse=True)

    signals_list = []
    for sig, stats in signal_map.items():
        wr = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        signals_list.append({
            "signal": sig,
            "trades": stats['trades'],
            "pnl": stats['pnl'],
            "win_rate": wr
        })
    
    # Sort signals by PnL descending
    signals_list.sort(key=lambda x: x['pnl'], reverse=True)

    return {
        "symbols": symbols_list,
        "signals": signals_list,
        "total_trades": total_trades,
        "total_pnl": total_pnl
    }

if __name__ == "__main__":
    import sys
    import os
    path = sys.argv[1] if len(sys.argv) > 1 else "backtest_results.json"
    if os.path.exists(path):
        res = analyze_results(path)
        print(json.dumps(res, indent=2))
    else:
        print(f"File not found: {path}")
