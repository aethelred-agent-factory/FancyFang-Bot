#!/usr/bin/env python3
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, TabbedContent, TabPane
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from rich.panel import Panel
from rich.console import Group
from core.ui_rich import (
    get_account_summary, 
    get_position_row, 
    get_market_overview, 
    get_performance_stats, 
    get_log_view
)
import datetime
import threading

class Dashboard(Static):
    """Main terminal dashboard with 2-column layout."""
    balance = reactive(0.0)
    upnl = reactive(0.0)
    locked_margin = reactive(0.0)
    entropy_penalty = reactive(0.0)
    positions = reactive([])
    market_data = reactive([])
    initial_balance = 1000.0

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left-pane", classes="column"):
                yield Static(id="account-summary")
                yield Static(id="market-overview")
            with Vertical(id="right-pane", classes="column"):
                yield Static(id="positions-header")
                yield Static(id="positions-list")

    def watch_balance(self, value: float) -> None: self.update_ui()
    def watch_upnl(self, value: float) -> None: self.update_ui()
    def watch_positions(self, value: list) -> None: self.update_ui()

    def update_ui(self) -> None:
        try:
            summary = self.query_one("#account-summary")
            summary.update(get_account_summary(self.balance, self.upnl, self.locked_margin, self.entropy_penalty, self.initial_balance))
            
            market = self.query_one("#market-overview")
            market.update(get_market_overview(self.market_data))
            
            pos_list = self.query_one("#positions-list")
            panels = [
                get_position_row(
                    p["symbol"], p["side"], p["entry"], p.get("current", p["entry"]),
                    p["size"], p["margin"], p.get("pnl", 0.0), p.get("stop_price", p["entry"])
                ) for p in self.positions
            ]
            pos_list.update(Group(*panels) if panels else Panel("Searching for signals...", border_style="fb.dim"))
        except:
            pass

class PerformanceStats(Static):
    """Page for deep performance analytics."""
    stats_data = reactive({})

    def render(self) -> Panel:
        return Panel(
            get_performance_stats(self.stats_data),
            title="[bold fb.cyan]PERFORMANCE ANALYTICS[/bold fb.cyan]",
            border_style="fb.cyan"
        )

class SystemLogs(Static):
    """Page for full-screen scrolling logs."""
    logs = reactive([])

    def render(self) -> Panel:
        return get_log_view(self.logs)

class FancyBotApp(App):
    """Professional Trading Terminal App."""
    
    CSS = """
    Screen { background: #000b1e; color: white; }
    .column { width: 1fr; padding: 1; }
    #left-pane { width: 35%; }
    #right-pane { width: 65%; }
    Static { margin-bottom: 1; }
    #positions-header { background: $primary; color: white; text-align: center; text-style: bold; height: 1; margin-bottom: 0; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("1", "switch_tab('dashboard')", "Dashboard"),
        ("2", "switch_tab('performance')", "Stats"),
        ("3", "switch_tab('logs')", "Logs"),
    ]

    def __init__(self, bot_state=None, bot_logs=None, initial_balance=1000.0):
        super().__init__()
        self.bot_state = bot_state
        self.bot_logs = bot_logs
        self.initial_balance = initial_balance

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="dashboard"):
            with TabPane("🚀 Dashboard", id="dashboard"):
                yield Dashboard(id="main-dashboard")
            with TabPane("📊 Performance", id="performance"):
                yield PerformanceStats(id="stats-view")
            with TabPane("📋 Logs", id="logs"):
                yield SystemLogs(id="system-logs")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#main-dashboard").initial_balance = self.initial_balance
        self.set_interval(1.0, self.update_from_bot)

    def update_from_bot(self) -> None:
        if not self.bot_state:
            return

        # Update Dashboard
        dash = self.query_one("#main-dashboard")
        with self.bot_state.lock:
            dash.balance = self.bot_state.balance
            dash.positions = self.bot_state.positions[:]
            dash.entropy_penalty = getattr(self.bot_state, 'entropy_penalty', 0.0)
            
            # Calculate total uPnL from positions
            total_upnl = 0.0
            locked_margin = 0.0
            for p in dash.positions:
                locked_margin += p.get("margin", 0.0)
                now = self.bot_state.live_prices.get(p["symbol"])
                if now:
                    upnl = (now - p['entry']) * p['size'] if p['side'] == "Buy" else (p['entry'] - now) * p['size']
                    total_upnl += upnl
                    p["current"] = now
                    p["pnl"] = upnl
            
            dash.upnl = total_upnl
            dash.locked_margin = locked_margin
            
            # Update Stats
            stats_view = self.query_one("#stats-view")
            wins = self.bot_state.rolling_stats["wins"]
            losses = self.bot_state.rolling_stats["losses"]
            total = wins + losses
            stats_view.stats_data = {
                "win_rate": (wins / total * 100) if total > 0 else 0,
                "profit_factor": (self.bot_state.rolling_stats["win_pnl"] / abs(self.bot_state.rolling_stats["loss_pnl"])) if self.bot_state.rolling_stats["loss_pnl"] != 0 else 0,
                "expectancy": (self.bot_state.rolling_stats["win_pnl"] + self.bot_state.rolling_stats["loss_pnl"]) / total if total > 0 else 0,
                "avg_win": self.bot_state.rolling_stats["win_pnl"] / wins if wins > 0 else 0,
                "avg_loss": self.bot_state.rolling_stats["loss_pnl"] / losses if losses > 0 else 0,
                "max_dd": 0.0 # To be implemented via drawdown_guard if needed
            }

        # Update Logs
        if self.bot_logs:
            self.query_one("#system-logs").logs = list(self.bot_logs)

    def action_switch_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

if __name__ == "__main__":
    # Mock for testing
    class MockState:
        def __init__(self):
            self.lock = threading.Lock()
            self.balance = 1200.0
            self.positions = []
            self.live_prices = {}
            self.rolling_stats = {"wins": 5, "losses": 3, "win_pnl": 150.0, "loss_pnl": -80.0}
    
    app = FancyBotApp(bot_state=MockState())
    app.run()
