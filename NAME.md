# FancyFang Bot — Name Origin & Canonical Identity

## The Official Name

**FancyFang Bot**

## How It Got Here

The name evolved entirely by accident across multiple AI prompting sessions, which feels appropriate for a codebase that was itself built entirely through AI prompting.

### The lineage:

1. **"Fang Blenny Bot"** — The original intended name. A Fang Blenny is a real fish: a small, venomous ambush predator that mimics harmless species to get close to prey before striking. The metaphor fit — a short scanner that waits for overextended setups and fades them.

2. **"Fancy Bot"** — An early Claude session misread "Fang Blenny" in the context window and generated the startup ASCII banner with "Fancy Bot" instead. The developer noticed but found it funny and left it.

3. **"FancyBlenny"** — Subsequent Claude sessions saw both "Fancy" and "Blenny" floating around in comments and docstrings and started blending them into a portmanteau. This persisted through several revision cycles and became the de facto internal name used throughout the codebase.

4. **"FancyFang Bot"** — The final settled name. Keeps the "Fancy" that AI accidentally introduced, restores the "Fang" from the original intent, drops "Blenny" to clean it up. Best of both lineages.

## Why This File Exists

Because the name history lived entirely in prompt session memory and would have been lost. Any human developer (or future AI session) picking up this codebase should know that:

- The name is **FancyFang Bot**
- The canonical ASCII banner lives in `banner.py`
- All modules import `BANNER` from `banner.py` — do not define local banner strings
- The naming confusion in older comments is historical artifact, not an error

## Project Identity

- **Full name:** FancyFang Bot
- **Exchange:** Phemex USDT-M Perpetuals
- **Strategy:** Short-biased momentum fade / mean reversion
- **Development method:** 100% AI-prompted (Claude / Anthropic), no hand-written code
