# Changelog - FancyBot Revised

## [2026-03-08.3] - Performance Tuning & Logic Fixes

### Fixed
- **Fast-Track Deadlock:** Implemented `try...finally` in `on_scan_result` to ensure symbols are always removed from `fast_track_opened`, preventing permanent margin lock-ups on verification failures.
- **Candidate Processing:** Changed `break` to `continue` in the simulation candidate loop, ensuring that one insufficient margin check doesn't block other eligible trades in the same scan.

### Changed
- **Entropy Deflator Tuning:** Reduced `ENTROPY_SAT_WEIGHT` (40 -> 30) and `ENTROPY_MAX_PENALTY` (40 -> 35) to allow high-quality setups (140+) to punch through during moderate market clusters.

## [2026-03-08.2] - Architectural Refactor & Stability Pass

### Fixed
- **Race Conditions:** Eliminated over eight independent manual threading locks in `sim_bot.py` by consolidating global state into a unified, thread-safe `SimBotState` class.
- **Synchronous I/O Latency:** Refactored `log_system_event` in `phemex_common.py` to use a background thread and queue, preventing disk I/O from blocking critical trading paths.
- **Syntax & Runtime Errors:** Fixed a NameError for `dataclass` and `_bot_logs` in `sim_bot.py`, and a syntax error in `drawdown_guard.py`'s f-string formatting.
- **Graceful Failures:** Replaced hard `sys.exit(1)` on dependency failure with a custom `InitializationError` for better modularity.

### Changed
- **State Management:** Refactored `sim_bot.py`, `risk_manager.py`, and `drawdown_guard.py` from module-level global variables to class-based architectures (`SimBotState`, `RiskManager`, `DrawdownGuard`).
- **I/O Efficiency:** Implemented an in-memory write-through cache for the paper account state in `sim_bot.py`, significantly reducing JSON parsing overhead in the main loop.
- **Externalized Configuration:** Moved magic numbers for Hawkes penalties, Leverage ATR thresholds, and Entropy Deflator parameters into configurable constants and environment variables.
- **Code Clarity:** Renamed terse module aliases (e.g., `_sa`, `_rm`, `_dd`) to descriptive names (`analytics`, `risk_mgr`, `drawdown_guard`) and implemented comprehensive PEP 484 type hints.

## [2026-03-08.1] - Logic Overhaul & UI Sync

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
