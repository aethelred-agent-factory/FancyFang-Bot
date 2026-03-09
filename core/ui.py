#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  FancyFangBot                            ║
# ║                                                                              ║
# ║  This file, and every file in this project, was written entirely through     ║
# ║  iterative AI prompting (Claude / Anthropic). No lines were written by       ║
# ║  hand. All architecture decisions, refactors, bug fixes, and feature         ║
# ║  additions were directed via natural-language prompts and implemented by     ║
# ║  AI. This is expected to remain the primary (and likely only) development    ║
# ║  method for this project for the foreseeable future.                         ║
# ║                                                                              ║
# ║  If you are a human developer reading this: the design intent and business   ║
# ║  logic live in the prompt history, not in comments. Treat this code as you   ║
# ║  would any LLM output — verify critical paths before trusting them.          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
"""
FancyFangBot UI Kit
─────────────────
Shared terminal display primitives used across all FancyFangBot scripts.
Import with:  import core.ui
"""

from colorama import Fore, Style

# phemex_common.grade is imported at module level to avoid per-call import lookups.
# If the import fails (e.g. during isolated unit tests), grade_badge falls back gracefully.
try:
    from core.phemex_common import grade as _pc_grade
except ImportError:
    _pc_grade = None

W = 96  # standard terminal width

# ── Horizontal rules ──────────────────────────────────────────────────────────
def hr_double(color=Fore.CYAN):
    """Return a double-line horizontal rule string."""
    return color + "═" * W + Style.RESET_ALL

def hr_thin(color=Fore.CYAN):
    """Return a thin horizontal rule string."""
    return color + "─" * W + Style.RESET_ALL

def hr_dash(color=""):
    """Return a dashed horizontal rule string."""
    return color + "┄" * W + Style.RESET_ALL

def hr_heavy():
    """Return a heavy horizontal rule string."""
    return Fore.WHITE + Style.BRIGHT + "━" * W + Style.RESET_ALL

# ── Score gauge ───────────────────────────────────────────────────────────────
def score_gauge(score: int, width: int = 24) -> str:
    """Visual bar gauge scaled 0–200."""
    clamped_score = max(0, min(score, 200))
    filled_width = int(clamped_score / 200 * width)
    empty_width = width - filled_width
    if score >= 145:
        gauge_color = Fore.LIGHTGREEN_EX
    elif score >= 120:
        gauge_color = Fore.GREEN
    elif score >= 100:
        gauge_color = Fore.YELLOW
    elif score >= 80:
        gauge_color = Fore.LIGHTYELLOW_EX
    else:
        gauge_color = Fore.RED
    return f"{gauge_color}{'█' * filled_width}{'░' * empty_width}{Style.RESET_ALL}"

# ── Mini sparkline (equity curve etc.) ───────────────────────────────────────
_SPARK_CHARS = "▁▂▃▄▅▆▇█"

def sparkline(values: list, width: int = 16) -> str:
    """Return a unicode sparkline string from a list of values."""
    if not values:
        return "─" * width
    low_value, high_value = min(values), max(values)
    value_span = high_value - low_value or 1
    spark_indices = [min(7, int((v - low_value) / value_span * 8)) for v in values[-width:]]
    return "".join(_SPARK_CHARS[i] for i in spark_indices)

# ── Grade badge ───────────────────────────────────────────────────────────────
_GRADE_COLORS = {
    "A": Fore.LIGHTGREEN_EX,
    "B": Fore.GREEN,
    "C": Fore.YELLOW,
    "D": Fore.RED,
}
def grade_badge(score: int) -> str:
    """Return a coloured ▐G▌ grade badge string."""
    if _pc_grade is None:
        return ""
    g, _ = _pc_grade(score)
    c = _GRADE_COLORS.get(g, Fore.WHITE)
    return f"{c}▐{g}▌{Style.RESET_ALL}"

# ── Section header ────────────────────────────────────────────────────────────
def section(title: str, color=Fore.CYAN, char="═") -> str:
    """Return a centered section header with horizontal padding."""
    pad   = f"  {title}  "
    side  = (W - len(pad)) // 2
    line  = char * side + pad + char * (W - side - len(pad))
    return color + Style.BRIGHT + line + Style.RESET_ALL

def section_left(title: str, color=Fore.CYAN) -> str:
    """Return a left-aligned section header string."""
    line = f"  {title}  " + "─" * max(0, W - len(title) - 4)
    return color + Style.BRIGHT + line + Style.RESET_ALL

# ── Coloured stat value ───────────────────────────────────────────────────────
def pnl_color(val: float) -> str:
    """Return Fore.LIGHTGREEN_EX for positive, Fore.RED for negative."""
    return Fore.LIGHTGREEN_EX if val > 0 else (Fore.RED if val < 0 else Fore.WHITE)

def colored(val, fmt="+.4f", pos_color=Fore.LIGHTGREEN_EX, neg_color=Fore.RED) -> str:
    """Return a formatted and coloured value string."""
    c = pos_color if float(val) >= 0 else neg_color
    return f"{c}{val:{fmt}}{Style.RESET_ALL}"

# ── Direction label ───────────────────────────────────────────────────────────
def dir_label(direction: str) -> str:
    """Return a coloured ▲ LONG or ▼ SHORT label."""
    if direction == "LONG":
        return f"{Fore.LIGHTGREEN_EX}▲ LONG{Style.RESET_ALL}"
    return f"{Fore.RED}▼ SHORT{Style.RESET_ALL}"

# ── Win-rate bar ──────────────────────────────────────────────────────────────
def wr_bar(pct: float, width: int = 20) -> str:
    """Return a horizontal bar gauge string for win rate."""
    filled = int(pct / 100 * width)
    color  = Fore.LIGHTGREEN_EX if pct >= 55 else (Fore.YELLOW if pct >= 45 else Fore.RED)
    return f"{color}{'█' * filled}{'░' * (width - filled)}{Style.RESET_ALL}"

# ── Box drawing helpers ───────────────────────────────────────────────────────
def box_top(w=W):
    """Return a top box-border string."""
    return Fore.CYAN + "┌" + "─" * (w - 2) + "┐" + Style.RESET_ALL

def box_mid(w=W):
    """Return a middle box-border string."""
    return Fore.CYAN + "├" + "─" * (w - 2) + "┤" + Style.RESET_ALL

def box_bot(w=W):
    """Return a bottom box-border string."""
    return Fore.CYAN + "└" + "─" * (w - 2) + "┘" + Style.RESET_ALL

def box_row(text, w=W):
    """Return a single row of a box containing text."""
    inner = w - 4
    return Fore.CYAN + "│ " + Style.RESET_ALL + f"{text:<{inner}}" + Fore.CYAN + " │" + Style.RESET_ALL

