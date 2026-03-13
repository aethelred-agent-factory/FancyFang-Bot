#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import re

from colorama import Fore, Style

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

# Regex to strip ANSI escape codes for accurate string length calculation
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(s):
    """Strip ANSI escape sequences from a string."""
    if not isinstance(s, str):
        return str(s)
    return ANSI_ESCAPE.sub("", s)


# phemex_common.grade is imported at module level to avoid per-call import lookups.
# If the import fails (e.g. during isolated unit tests), grade_badge falls back gracefully.
try:
    from core.phemex_common import grade as _pc_grade
except ImportError:
    _pc_grade = None

W = 96  # standard terminal width


# ── Horizontal rules ──────────────────────────────────────────────────────────
def hr_double(color=Fore.CYAN, width: int = W):
    """Return a double-line horizontal rule string."""
    return color + "═" * width + Style.RESET_ALL


def hr_thin(color=Fore.CYAN, width: int = W):
    """Return a thin horizontal rule string."""
    return color + "─" * width + Style.RESET_ALL


def hr_dash(color="", width: int = W):
    """Return a dashed horizontal rule string."""
    return color + "┄" * width + Style.RESET_ALL


def hr_heavy(width: int = W):
    """Return a heavy horizontal rule string."""
    return Fore.WHITE + Style.BRIGHT + "━" * width + Style.RESET_ALL


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
    spark_indices = [
        min(7, int((v - low_value) / value_span * 8)) for v in values[-width:]
    ]
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
    pad = f"  {title}  "
    side = (W - len(pad)) // 2
    line = char * side + pad + char * (W - side - len(pad))
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
    color = (
        Fore.LIGHTGREEN_EX if pct >= 55 else (Fore.YELLOW if pct >= 45 else Fore.RED)
    )
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
    visible_len = len(strip_ansi(text))
    padding = " " * max(0, inner - visible_len)
    return (
        Fore.CYAN
        + "│ "
        + Style.RESET_ALL
        + text
        + padding
        + Fore.CYAN
        + " │"
        + Style.RESET_ALL
    )


# ── Modern Panel ──────────────────────────────────────────────────────────────
def modern_panel(title: str, lines: list, color=Fore.CYAN, width: int = W) -> str:
    """Return a stylized box panel with a title and content lines."""
    out = []
    inner_w = width - 4

    # Top border with title
    title_str = f" {title} " if title else ""
    side_bars = (width - 4 - len(strip_ansi(title_str))) // 2
    top = (
        color
        + "┏"
        + "━" * side_bars
        + title_str
        + "━" * (width - 2 - side_bars - len(strip_ansi(title_str)))
        + "┓"
        + Style.RESET_ALL
    )
    out.append(top)

    for line in lines:
        visible_len = len(strip_ansi(line))
        padding = " " * max(0, inner_w - visible_len)
        out.append(
            color
            + "┃ "
            + Style.RESET_ALL
            + line
            + padding
            + color
            + " ┃"
            + Style.RESET_ALL
        )

    # Bottom border
    out.append(color + "┗" + "━" * (width - 2) + "┛" + Style.RESET_ALL)
    return "\n".join(out)


# ── Truecolor Gradients ────────────────────────────────────────────────────────
def gradient_text(text: str, start_rgb: tuple, end_rgb: tuple) -> str:
    """Return text with a horizontal truecolor gradient."""
    if not text:
        return ""
    r1, g1, b1 = start_rgb
    r2, g2, b2 = end_rgb
    n = len(text)
    out = ""
    for i, char in enumerate(text):
        if n > 1:
            r = int(r1 + (r2 - r1) * i / (n - 1))
            g = int(g1 + (g2 - g1) * i / (n - 1))
            b = int(b1 + (b2 - b1) * i / (n - 1))
        else:
            r, g, b = r1, g1, b1
        out += f"\033[38;2;{r};{g};{b}m{char}"
    return out + Style.RESET_ALL


# ── Braille PnL Chart ─────────────────────────────────────────────────────────
BRAILLE_MAP = [
    [0x01, 0x08],
    [0x02, 0x10],
    [0x04, 0x20],
    [0x40, 0x80],
]


def _to_braille(left_row: int, right_row: int) -> str:
    """Convert two column bit patterns into a braille unicode char."""
    bits = 0
    for row in range(4):
        if left_row & (1 << row):
            bits |= BRAILLE_MAP[row][0]
        if right_row & (1 << row):
            bits |= BRAILLE_MAP[row][1]
    return chr(0x2800 + bits)


def render_pnl_chart(
    pnl_history: list,
    width: int = 40,
    height: int = 4,
    label: str = "",
) -> list:
    """
    Renders a smooth braille PnL line chart.
    Returns list of strings (one per row).
    """
    if not pnl_history:
        pnl_history = [0.0]

    # Pad or trim to fit width*2 data points (2 per char cell)
    points = pnl_history[-(width * 2) :]
    while len(points) < width * 2:
        points = [points[0]] * (width * 2 - len(points)) + points

    lo = min(points)
    hi = max(points)
    span = (hi - lo) or 1e-10
    rows = height * 4

    def to_row(v):
        return int((v - lo) / span * (rows - 1))

    scaled = [to_row(p) for p in points]
    grid = [[[0, 0] for _ in range(width)] for _ in range(height)]

    for col_idx in range(width):
        left_val = scaled[col_idx * 2]
        right_val = scaled[col_idx * 2 + 1]
        for val, side in [(left_val, 0), (right_val, 1)]:
            char_row = height - 1 - (val // 4)
            dot_row = val % 4
            char_row = max(0, min(height - 1, char_row))
            grid[char_row][col_idx][side] |= 1 << dot_row

    zero_char_row = height - 1 - (to_row(0.0) // 4)
    lines = []
    current_pnl = pnl_history[-1]
    chart_color = Fore.LIGHTGREEN_EX if current_pnl >= 0 else Fore.RED

    for row_idx in range(height):
        row_str = ""
        for col_idx in range(width):
            row_str += _to_braille(grid[row_idx][col_idx][0], grid[row_idx][col_idx][1])

        prefix = Fore.CYAN + "│" + Style.RESET_ALL
        if row_idx == zero_char_row:
            lines.append(
                prefix
                + chart_color
                + row_str
                + Style.RESET_ALL
                + Fore.YELLOW
                + " 0.00"
                + Style.RESET_ALL
            )
        else:
            lines.append(prefix + chart_color + row_str + Style.RESET_ALL)

    return lines


def render_price_line(
    current_price: float,
    stop_price: float,
    take_profit: float,
    pnl_val: float,
    width: int = 40,
) -> str:
    """
    Renders a line showing current price position relative to SL and TP.
    Shows the PnL amount moving across the line.
    [X] -------- $0.45 -------- [$]
    """
    # Range is from stop_price to take_profit
    total_range = abs(take_profit - stop_price) or 1e-10

    # Calculate progress (0.0 at SL, 1.0 at TP)
    if stop_price < take_profit:  # LONG
        progress = (current_price - stop_price) / total_range
    else:  # SHORT
        progress = (stop_price - current_price) / total_range

    progress = max(0.0, min(1.0, progress))

    # Inner width for the line (excluding ends and spaces)
    # [X] (3) + space (1) + line + space (1) + [$] (3) = 8 chars overhead
    inner_w = max(10, width - 8)
    pos = int(progress * inner_w)

    pnl_str = f"${abs(pnl_val):.2f}"
    pnl_len = len(pnl_str)

    # Ensure PnL string fits
    if pnl_len + 2 > inner_w:
        pnl_str = f"{abs(pnl_val):.1f}"
        pnl_len = len(pnl_str)

    # Determine position for the PnL string label
    # We want to center the label at 'pos', but keep it within [0, inner_w - pnl_len]
    start_label = pos - (pnl_len // 2)
    start_label = max(0, min(inner_w - pnl_len, start_label))

    # Construct the line with the PnL label embedded
    # We can't easily embed colored text into a list of chars without breaking index math
    # So we'll slice strings

    left_dash = "─" * start_label
    mid_label = f" {pnl_color(pnl_val)}{pnl_str}{Style.RESET_ALL} "
    right_dash = "─" * max(0, inner_w - (start_label + pnl_len))

    full_line = f"{Fore.RED}[X]{Style.RESET_ALL} {Fore.CYAN}{left_dash}{Style.RESET_ALL}{mid_label}{Fore.CYAN}{right_dash}{Style.RESET_ALL} {Fore.GREEN}[$]{Style.RESET_ALL}"
    return full_line


# ── Advanced Visual Primitives ────────────────────────────────────────────────


def braille_progress_bar(pct: float, width: int = 20) -> str:
    """High-resolution progress bar using braille characters (2 dots per cell)."""
    pct = max(0, min(100, pct))
    total_steps = width * 2
    filled_steps = int((pct / 100) * total_steps)

    out = ""
    for i in range(width):
        left_idx = i * 2
        right_idx = i * 2 + 1

        left_filled = left_idx < filled_steps
        right_filled = right_idx < filled_steps

        bits = 0
        if left_filled:
            bits |= 0x01 | 0x02 | 0x04 | 0x40
        if right_filled:
            bits |= 0x08 | 0x10 | 0x20 | 0x80

        out += chr(0x2800 + bits)

    # Dynamic RGB color: Red (0%) -> Yellow (50%) -> Green (100%)
    if pct < 50:
        r, g, b = 255, int(255 * (pct / 50)), 0
    else:
        r, g, b = int(255 * (1 - (pct - 50) / 50)), 255, 0

    return f"\033[38;2;{r};{g};{b}m{out}{Style.RESET_ALL}"


def glow_panel(
    title: str, lines: list, color_rgb: tuple = (0, 255, 255), width: int = W
) -> str:
    """Stylized panel with a truecolor 'glowing' border."""
    r, g, b = color_rgb

    def get_rgb(r, g, b):
        return f"\033[38;2;{r};{g};{b}m"

    out = []
    inner_w = width - 4
    border_color = get_rgb(r, g, b)

    # Top border with title
    title_str = f" {title} " if title else ""
    side_bars = (width - 4 - len(strip_ansi(title_str))) // 2
    top = (
        border_color
        + "💠"
        + "━" * side_bars
        + Style.BRIGHT
        + title_str
        + Style.NORMAL
        + "━" * (width - 4 - side_bars - len(strip_ansi(title_str)))
        + "💠"
        + Style.RESET_ALL
    )
    out.append(top)

    for line in lines:
        visible_len = len(strip_ansi(line))
        padding = " " * max(0, inner_w - visible_len)
        out.append(
            border_color
            + "┃ "
            + Style.RESET_ALL
            + line
            + padding
            + border_color
            + " ┃"
            + Style.RESET_ALL
        )

    # Bottom border
    bottom = border_color + "┗" + "━" * (width - 2) + "┛" + Style.RESET_ALL
    out.append(bottom)

    return "\n".join(out)


def cyber_telemetry(label: str, value: float, target: float, unit: str = "") -> str:
    """Compact stylized telemetry indicator with label, bar and value."""
    pct = (value / target * 100) if target != 0 else 0
    bar = braille_progress_bar(pct, width=10)

    color = pnl_color(value)
    if unit == "$":
        val_display = f"${abs(value):.2f}"
        if value < 0:
            val_display = "-" + val_display
    else:
        val_display = f"{value:.2f}{unit}"

    return f"{Fore.CYAN}{label.upper():<10}{Style.RESET_ALL} [{bar}] {color}{val_display:>10}{Style.RESET_ALL}"
