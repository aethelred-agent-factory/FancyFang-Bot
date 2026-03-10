#!/usr/bin/env python3
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.progress import Progress, BarColumn, TextColumn
from rich.console import Group
from rich.theme import Theme
from rich.style import Style
from rich.align import Align
from typing import List
import datetime

# Custom theme for FancyBot - mapping semantic names to specific colors/styles
FB_THEME = Theme({
    "fb.cyan": "cyan",
    "fb.magenta": "magenta",
    "fb.green": "bright_green",
    "fb.red": "bright_red",
    "fb.yellow": "yellow",
    "fb.white": "white",
    "fb.dim": "dim white",
})

def get_account_summary(balance: float, upnl: float, locked_margin: float, entropy_penalty: float, initial_balance: float):
    """Returns a Rich Panel containing the account summary."""
    equity = balance + locked_margin + upnl
    
    # Wallet Telemetry
    wallet_pct = (balance / (initial_balance * 2)) * 100
    wallet_style = "fb.green" if balance >= initial_balance else "fb.yellow"
    
    # uPnL Telemetry
    upnl_pct = (abs(upnl) / (initial_balance * 0.1)) * 100 if initial_balance > 0 else 0
    upnl_style = "fb.green" if upnl >= 0 else "fb.red"
    
    table = Table.grid(expand=True)
    table.add_column(width=12) # Label
    table.add_column(ratio=1)  # Bar
    table.add_column(width=12, justify="right") # Value

    # Helper for telemetry rows
    def add_telemetry_row(label: str, value_str: str, pct: float, style_name: str):
        progress = Progress(
            BarColumn(bar_width=None, complete_style=style_name, finished_style=style_name),
            expand=True
        )
        task = progress.add_task("", total=100, completed=min(100, max(0, pct)))
        table.add_row(
            Text(label.upper(), style="fb.cyan"),
            progress,
            Text(value_str, style=style_name)
        )

    add_telemetry_row("Wallet", f"${balance:.2f}", wallet_pct, wallet_style)
    add_telemetry_row("uPnL", f"${upnl:+.2f}", upnl_pct, upnl_style)
    
    stats_line = Text.assemble(
        (" Equity: ", "fb.white"),
        (f"${equity:.2f}", "bold fb.white"),
        (" | Margin: ", "fb.white"),
        (f"${locked_margin:.1f}", "fb.yellow"),
        (" | Entropy: ", "fb.white"),
        (f"{entropy_penalty:.2f}", "fb.magenta")
    )

    content = Group(table, stats_line)
    
    return Panel(
        content,
        title="[bold fb.cyan]SYSTEM CORE[/bold fb.cyan]",
        border_style="fb.cyan",
        padding=(1, 2)
    )

def get_position_row(symbol: str, side: str, entry: float, current: float, size: float, margin: float, pnl: float, stop_price: float):
    """Returns a Rich Panel for a single position."""
    pnl_style = "fb.green" if pnl >= 0 else "fb.red"
    side_text = "▲ LONG" if side == "Buy" else "▼ SHORT"
    side_style = "fb.green" if side == "Buy" else "fb.red"
    
    # Progress towards stop loss
    total_range = abs(entry - stop_price) or 1e-10
    dist_to_stop = abs(current - stop_price)
    stop_pct = (dist_to_stop / total_range) * 100
    stop_style = "fb.green" if stop_pct > 50 else ("fb.yellow" if stop_pct > 25 else "fb.red")

    header = Table.grid(expand=True)
    header.add_row(
        Text(symbol, style="bold fb.white"),
        Align.right(Text(side_text, style=side_style))
    )
    
    # Progress Bar for Stop Loss
    stop_progress = Progress(
        TextColumn("[fb.dim]Distance to Stop[/fb.dim]"),
        BarColumn(bar_width=None, complete_style=stop_style, finished_style=stop_style),
        TextColumn(f"[bold {stop_style}]{stop_pct:.1f}%[/bold {stop_style}]"),
        expand=True
    )
    stop_progress.add_task("", total=100, completed=min(100, max(0, stop_pct)))

    details = Text.assemble(
        ("Entry: ", "fb.dim"), (f"{entry:.4g}", "fb.white"),
        ("  Price: ", "fb.dim"), (f"{current:.4g}", "fb.white"),
        ("  PnL: ", "fb.dim"), (f"${pnl:+.2f}", f"bold {pnl_style}")
    )

    return Panel(
        Group(header, stop_progress, details),
        border_style="fb.dim",
        padding=(0, 1)
    )

def get_market_overview(ticker_data: List[dict]):
    """Returns a table of top market movers/tickers."""
    table = Table(expand=True, box=None, padding=(0, 1))
    table.add_column("Symbol", style="bold fb.white")
    table.add_column("Price", justify="right")
    table.add_column("24h %", justify="right")
    table.add_column("Vol", justify="right", style="fb.dim")
    
    for t in ticker_data[:10]:
        change = t.get("change", 0.0)
        color = "fb.green" if change >= 0 else "fb.red"
        table.add_row(
            t["symbol"],
            f"{t['price']:.4g}",
            Text(f"{change:+.2f}%", style=color),
            f"{t['vol_24h']/1e6:.1f}M"
        )
    
    return Panel(table, title="[bold fb.cyan]MARKET PULSE[/bold fb.cyan]", border_style="fb.cyan")

def get_performance_stats(stats: dict):
    """Returns a grid of key performance metrics."""
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    def stat_box(label: str, value: str, color: str):
        return Panel(
            Align.center(Text(value, style=f"bold {color}", justify="center")),
            title=f"[fb.dim]{label}[/fb.dim]",
            border_style="fb.dim"
        )

    grid.add_row(
        stat_box("Win Rate", f"{stats.get('win_rate', 0):.1f}%", "fb.green"),
        stat_box("Profit Factor", f"{stats.get('profit_factor', 0):.2f}", "fb.yellow"),
        stat_box("Expectancy", f"${stats.get('expectancy', 0):.2f}", "fb.cyan")
    )
    grid.add_row(
        stat_box("Avg Win", f"${stats.get('avg_win', 0):.2f}", "fb.green"),
        stat_box("Avg Loss", f"${stats.get('avg_loss', 0):.2f}", "fb.red"),
        stat_box("Max DD", f"{stats.get('max_dd', 0):.1f}%", "fb.magenta")
    )

    return grid

def get_log_view(logs: List[str]):
    """Returns a clean log panel."""
    log_text = Text()
    for line in logs[-20:]:
        # Simple color parsing for log levels
        style = "fb.white"
        if "ERROR" in line or "FAIL" in line: style = "fb.red"
        elif "WARNING" in line: style = "fb.yellow"
        elif "SUCCESS" in line or "VERIFIED" in line: style = "fb.green"
        elif "INFO" in line: style = "fb.cyan"
        
        log_text.append(line + "\n", style=style)
    
    return Panel(log_text, title="[bold fb.cyan]SYSTEM AUDIT[/bold fb.cyan]", border_style="fb.dim")
