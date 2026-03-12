#!/usr/bin/env python3
from typing import List

from core.debug_log import dbg_log
from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Custom theme for FancyBot - mapping semantic names to specific colors/styles
# Note: We keep this for standalone Rich usage, but will use standard names in components for Textual portability
FB_THEME = Theme(
    {
        "fb.cyan": "cyan",
        "fb.magenta": "magenta",
        "fb.green": "bright_green",
        "fb.red": "bright_red",
        "fb.yellow": "yellow",
        "fb.white": "white",
        "fb.dim": "dim white",
    }
)

# Standard colors to use within functions for Textual compatibility
C_CYAN = "cyan"
C_MAGENTA = "magenta"
C_GREEN = "bright_green"
C_RED = "bright_red"
C_YELLOW = "yellow"
C_WHITE = "white"
C_DIM = "dim white"


def get_account_summary(
    balance: float,
    upnl: float,
    locked_margin: float,
    entropy_penalty: float,
    initial_balance: float,
):
    """Returns a Rich Panel containing the account summary."""
    equity = balance + locked_margin + upnl

    # Wallet Telemetry
    if not initial_balance or initial_balance <= 0:
        # region agent log
        dbg_log(
            hypothesisId="C",
            location="core/ui_rich.py:get_account_summary",
            message="initial_balance non-positive; wallet_pct fallback",
            data={"initial_balance": initial_balance, "balance": balance},
        )
        # endregion
        wallet_pct = 0.0
    else:
        wallet_pct = (balance / (initial_balance * 2)) * 100
    wallet_style = C_GREEN if balance >= initial_balance else C_YELLOW

    # uPnL Telemetry
    upnl_pct = (abs(upnl) / (initial_balance * 0.1)) * 100 if initial_balance > 0 else 0
    upnl_style = C_GREEN if upnl >= 0 else C_RED

    table = Table.grid(expand=True)
    table.add_column(width=12)  # Label
    table.add_column(ratio=1)  # Bar
    table.add_column(width=12, justify="right")  # Value

    # Helper for telemetry rows
    def add_telemetry_row(label: str, value_str: str, pct: float, style_name: str):
        progress = Progress(
            BarColumn(
                bar_width=None, complete_style=style_name, finished_style=style_name
            ),
            expand=True,
        )
        task = progress.add_task("", total=100, completed=min(100, max(0, pct)))
        table.add_row(
            Text(label.upper(), style=C_CYAN),
            progress,
            Text(value_str, style=style_name),
        )

    add_telemetry_row("Wallet", f"${balance:.2f}", wallet_pct, wallet_style)
    add_telemetry_row("uPnL", f"${upnl:+.2f}", upnl_pct, upnl_style)

    stats_line = Text.assemble(
        (" Equity: ", C_WHITE),
        (f"${equity:.2f}", f"bold {C_WHITE}"),
        (" | Margin: ", C_WHITE),
        (f"${locked_margin:.1f}", C_YELLOW),
        (" | Entropy: ", C_WHITE),
        (f"{entropy_penalty:.2f}", C_MAGENTA),
    )

    content = Group(table, stats_line)

    return Panel(
        content,
        title=f"[bold {C_CYAN}]SYSTEM CORE[/bold {C_CYAN}]",
        border_style=C_CYAN,
        padding=(1, 2),
    )


def get_position_row(
    symbol: str,
    side: str,
    entry: float,
    current: float,
    size: float,
    margin: float,
    pnl: float,
    stop_price: float,
):
    """Returns a Rich Panel for a single position."""
    pnl_style = C_GREEN if pnl >= 0 else C_RED
    side_text = "▲ LONG" if side == "Buy" else "▼ SHORT"
    side_style = C_GREEN if side == "Buy" else C_RED

    # Progress towards stop loss
    total_range = abs(entry - stop_price) or 1e-10
    dist_to_stop = abs(current - stop_price)
    stop_pct = (dist_to_stop / total_range) * 100
    stop_style = C_GREEN if stop_pct > 50 else (C_YELLOW if stop_pct > 25 else C_RED)

    header = Table.grid(expand=True)
    header.add_row(
        Text(symbol, style=f"bold {C_WHITE}"),
        Align.right(Text(side_text, style=side_style)),
    )

    # Progress Bar for Stop Loss
    stop_progress = Progress(
        TextColumn(f"[{C_DIM}]Distance to Stop[/{C_DIM}]"),
        BarColumn(bar_width=None, complete_style=stop_style, finished_style=stop_style),
        TextColumn(f"[bold {stop_style}]{stop_pct:.1f}%[/bold {stop_style}]"),
        expand=True,
    )
    stop_progress.add_task("", total=100, completed=min(100, max(0, stop_pct)))

    details = Text.assemble(
        ("Entry: ", C_DIM),
        (f"{entry:.4g}", C_WHITE),
        ("  Price: ", C_DIM),
        (f"{current:.4g}", C_WHITE),
        ("  PnL: ", C_DIM),
        (f"${pnl:+.2f}", f"bold {pnl_style}"),
    )

    return Panel(
        Group(header, stop_progress, details), border_style=C_DIM, padding=(0, 1)
    )


def get_market_overview(ticker_data: List[dict]):
    """Returns a table of top market movers/tickers."""
    table = Table(expand=True, box=None, padding=(0, 1))
    table.add_column("Symbol", style=f"bold {C_WHITE}")
    table.add_column("Price", justify="right")
    table.add_column("24h %", justify="right")
    table.add_column("Vol", justify="right", style=C_DIM)

    for t in (ticker_data or [])[:10]:
        try:
            change = float(t.get("change", 0.0))
            color = C_GREEN if change >= 0 else C_RED
            table.add_row(
                t["symbol"],
                f"{float(t['price']):.4g}",
                Text(f"{change:+.2f}%", style=color),
                f"{float(t['vol_24h'])/1e6:.1f}M",
            )
        except Exception as e:
            # region agent log
            dbg_log(
                hypothesisId="B",
                location="core/ui_rich.py:get_market_overview",
                message="ticker row render failed",
                data={
                    "exc_type": type(e).__name__,
                    "exc": repr(e),
                    "ticker_keys": list(t.keys()) if isinstance(t, dict) else None,
                },
            )
            # endregion

    return Panel(
        table, title=f"[bold {C_CYAN}]MARKET PULSE[/bold {C_CYAN}]", border_style=C_CYAN
    )


def get_performance_stats(stats: dict):
    """Returns a grid of key performance metrics."""
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    def stat_box(label: str, value: str, color: str):
        return Panel(
            Align.center(Text(value, style=f"bold {color}", justify="center")),
            title=f"[{C_DIM}]{label}[/{C_DIM}]",
            border_style=C_DIM,
        )

    grid.add_row(
        stat_box("Win Rate", f"{stats.get('win_rate', 0):.1f}%", C_GREEN),
        stat_box("Profit Factor", f"{stats.get('profit_factor', 0):.2f}", C_YELLOW),
        stat_box("Expectancy", f"${stats.get('expectancy', 0):.2f}", C_CYAN),
    )
    grid.add_row(
        stat_box("Avg Win", f"${stats.get('avg_win', 0):.2f}", C_GREEN),
        stat_box("Avg Loss", f"${stats.get('avg_loss', 0):.2f}", C_RED),
        stat_box("Max DD", f"{stats.get('max_dd', 0):.1f}%", C_MAGENTA),
    )

    return grid


def get_log_view(logs: List[str]):
    """Returns a clean log panel."""
    log_text = Text()
    for line in logs[-20:]:
        # Simple color parsing for log levels
        style = C_WHITE
        if "ERROR" in line or "FAIL" in line:
            style = C_RED
        elif "WARNING" in line:
            style = C_YELLOW
        elif "SUCCESS" in line or "VERIFIED" in line:
            style = C_GREEN
        elif "INFO" in line:
            style = C_CYAN

        log_text.append(line + "\n", style=style)

    return Panel(
        log_text,
        title=f"[bold {C_CYAN}]SYSTEM AUDIT[/bold {C_CYAN}]",
        border_style=C_DIM,
    )
