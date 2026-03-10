import json
from collections import defaultdict

def analyze_results(filepath):
    try:
        with open(filepath, 'r') as f:
            trades = json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return

    symbol_stats = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})
    signal_stats = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0})

    for trade in trades:
        sym = trade.get('symbol')
        pnl = trade.get('pnl_usdt')
        if sym is None or pnl is None:
            continue

        symbol_stats[sym]["pnl"] += pnl
        symbol_stats[sym]["trades"] += 1
        if pnl > 0:
            symbol_stats[sym]["wins"] += 1

        for signal in trade.get('signals', []):
            # Clean up signal string (remove values)
            sig_name = signal.split('(')[0].strip()
            signal_stats[sig_name]["pnl"] += pnl
            signal_stats[sig_name]["trades"] += 1
            if pnl > 0:
                signal_stats[sig_name]["wins"] += 1

    print(f"\n{'='*60}")
    print(f"{'SYMBOL PERFORMANCE':<20} {'TRADES':<10} {'WR%':<10} {'PNL':<10}")
    print(f"{'-'*60}")
    sorted_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    for sym, stats in sorted_symbols:
        wr = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        print(f"{sym:<20} {stats['trades']:<10} {wr:>5.1f}%    {stats['pnl']:>10.4f}")

    print(f"\n{'='*60}")
    print(f"{'SIGNAL PERFORMANCE':<30} {'TRADES':<10} {'PNL':<10}")
    print(f"{'-'*60}")
    sorted_signals = sorted(signal_stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
    for sig, stats in sorted_signals:
        print(f"{sig:<30} {stats['trades']:<10} {stats['pnl']:>10.4f}")

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "trade_log_1h.json"
    analyze_results(path)
