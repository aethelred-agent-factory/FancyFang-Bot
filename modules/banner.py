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
#!/usr/bin/env python3
"""
banner.py — Canonical ASCII art banner for FancyFang Bot.

Single source of truth for the project name graphic.
All modules that display the startup banner import BANNER from here.

See NAME.md for the full story behind the name.
"""

BANNER = r"""
########    ###    ##    ##  ######  ##    ## ########    ###    ##    ##  ######      ########   #######  ########
##         ## ##   ###   ## ##    ##  ##  ##  ##         ## ##   ###   ## ##    ##     ##     ## ##     ##    ##
##        ##   ##  ####  ## ##         ####   ##        ##   ##  ####  ## ##           ##     ## ##     ##    ##
######   ##     ## ## ## ## ##          ##    ######   ##     ## ## ## ## ##   ####    ########  ##     ##    ##
##       ######### ##  #### ##          ##    ##       ######### ##  #### ##    ##     ##     ## ##     ##    ##
##       ##     ## ##   ### ##    ##    ##    ##       ##     ## ##   ### ##    ##     ##     ## ##     ##    ##
##       ##     ## ##    ##  ######     ##    ##       ##     ## ##    ##  ######      ########   #######     ##
"""

def get_gradient_banner():
    """Returns the banner pre-colorized with a cyan-magenta gradient."""
    try:
        from core.ui import gradient_text
        lines = BANNER.strip("\n").split("\n")
        colorized = [gradient_text(line, (0, 255, 255), (255, 0, 255)) for line in lines]
        return "\n".join(colorized)
    except ImportError:
        return BANNER
