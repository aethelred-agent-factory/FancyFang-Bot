# Changelog - FancyBot Revised

## [2026-03-08] - Logic Overhaul & UI Sync

### Added
- **Dynamic ATR Leverage:** Implemented `pick_sim_leverage` in `sim_bot.py`. Leverage (5x to 30x) is now automatically selected based on asset volatility (ATR%) and volume spikes.
- **Margin-Based Gating:** Added `get_sim_free_margin` and `BOT_MIN_FREE_MARGIN` threshold. The bot now gates entries based on available capital rather than a hardcoded position count.
- **Leverage Display:** Added leverage tracking to the paper account state and integrated it into the TUI (both the main positions list and the bottom consolidated view).
- **Banner Sync:** Synchronized `animations.py` to use the canonical ASCII `BANNER` from `banner.py` for the boot sequence.

### Changed
- **Unconditional Scanning:** Removed the `available_slots` restriction in the main loop. The bot now scans the market every interval regardless of the number of open positions.
- **Position Sizing:** Updated the legacy Kelly/fallback sizing logic. Trades are now capped at 20% of account balance instead of being divided by a fixed position limit.
- **Clean Slate:** Performed a full "factory reset" by clearing all historical logs (`.log`), trade results (`.json`, `.jsonl`), and `__pycache__` files.

### Security
- Retained `.env` configurations (API keys and Telegram credentials) throughout the reset and update process.
