# Changelog - FancyBot Revised

## [2026-03-08.4] - Architectural Stewardship & UTC Standardisation

### Added
- **Architectural Audit:** Completed a Deep Static Analysis of the entire core engine (`Audit_and_Revision_Log_[2026-03-08_1903]`).
- **Baseline Test Suite:** Initialized `tests/` directory with unit tests for `phemex_common.py` and `drawdown_guard.py` to ensure regression safety.

### Fixed
- **Deadlock Risks:** Resolved potential "Deadlock Freeze" by ensuring `file_io_lock` is always external to `lock` / `_lock` in `sim_bot.py`.
- **Silent Thread Failures:** Wrapped all background workers (WebSocket, Cache, TUI) in `try...except` blocks with full `traceback.format_exc()` logging.
- **Race Safety:** Refactored high-frequency paths in `p_bot.py` to identify missing prices and fetch them via REST outside of critical price-cache locks.

### Changed
- **UTC Standardisation:** Migrated every `datetime.now()` call to timezone-aware `datetime.now(datetime.timezone.utc)` for temporal consistency across logging and JSON storage.
- **Descriptive Naming:** Performed a codebase-wide refactor of terse variables to descriptive ones (e.g., `nb` -> `new_balance`, `r` -> `scan_res`, `v` -> `volume`).
- **Ruff Compliance:** Resolved over 100 linting violations including spacing, redundant semicolons, and unused imports to achieve "Senior Developer" code quality standards.

## [2026-03-08.4] - Hardware Integration (RP2040)

### Added
- **Physical Signaling:** Integrated RP2040 (Raspberry Pi Pico) support via `hardware_bridge.py`. The bot now provides real-time visual feedback using the onboard LED:
    - **Rapid Blinking:** Bot Startup / Initialization.
    - **Solid Light:** Active Position open.
    - **Fast Pulse:** Take Profit (Win).
    - **Slow "Sad" Pulse:** Stop Loss (Loss).
    - **Triple Blip:** Manual "Close All" command issued.

### Fixed
- **Deadlock Prevention:** Switched `state.lock` to `threading.RLock` and narrowed the lock scope in trade execution paths to prevent I/O deadlocks with `file_io_lock`.
- **NameError:** Resolved missing `fee` variable in simulation setup.

## [2026-03-08.3] - Performance Tuning & Logic Fixes

### Fixed
- **Analysis Crash:** Resolved a critical `NameError` (`fr_change`) in `phemex_common.unified_analyse` that was causing every ticker analysis to fail silently, resulting in empty scan results (`W0 L0`).
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

## [2026-03-08.5] - Systemic Grooming & SQLite Evolution

### Added
- **Storage Layer:** Implemented `StorageManager` in `storage_manager.py` using SQLite for robust, atomic, and efficient data persistence.
- **Regression Tests:** Added `tests/test_backtest_scoring.py` and `tests/test_storage_manager.py` to ensure core logic stability.

### Fixed
- **NameErrors:** Resolved critical `pc.BANNER` NameError in `backtest.py` and undefined `account` in `sim_bot.py`.
- **Redundant Imports:** Removed duplicate `hardware_bridge` import in `sim_bot.py`.

### Changed
- **Systemic Grooming:** Performed a codebase-wide refactor of `backtest.py` and `animations.py` to resolve all Ruff violations and rename terse variables (e.g., `l` -> `low_period`, `g` -> `gain_period`).
- **Numpy Optimization:** Refactored math-heavy indicator loops in `backtest.py` to use `numpy` vectorization for significant performance gains.
- **Storage Migration:** Refactored `sim_bot.py` to use the new SQLite `StorageManager` for account state and trade history, improving I/O performance and data integrity.
- **Banner Standardization:** Standardized all banner displays to use `BANNER` from `banner.py` as the single source of truth.
