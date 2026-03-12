# Ghost Trade / Entry-Exit Reconciliation Bug Fix

**Date:** March 12, 2026  
**Status:** FIXED ✅

## Problem Statement

The simulation bot (`sim_bot.py`) was experiencing a "ghost trade" bug where positions opened and closed by the live `p_bot` process would bleed into the simulation's ledger, causing spurious close events and reconciliation failures.

### Root Cause

Both `sim_bot` and `p_bot` were writing to the same shared SQLite database (`fancybot.db`). When:
1. The live bot opened a position on BTC at 50,000
2. The live bot closed that position at 50,500
3. The sim bot would see that close event in the shared database and attempt to reconcile it against the simulation's open positions

This resulted in:
- Undefined behavior if the sim had no open BTC position to match
- Potential false close records with invalid metadata (direction="Unknown", score=0)
- Reconciliation failures and ledger corruption

## Solution Implemented

### 1. Isolated Account Storage (JSON-based)

**File:** `core/sim_bot.py` → `SimBotState.load_account()` and `save_account()`

The simulation now maintains a dedicated JSON file (`data/state/paper_account.json`) for its account state:

```python
PAPER_ACCOUNT_FILE = SCRIPT_DIR.parent / "data" / "state" / "paper_account.json"
```

**Load Logic:**
- If JSON file exists → load from JSON (primary source of truth)
- If JSON file missing → start fresh with initial balance
- If DB contains orphaned positions when JSON is absent → log a warning (no import)

**Save Logic:**
- Primary: write JSON file
- Secondary: also update shared DB (for backwards-compatibility with analytics modules)

This ensures complete separation: the sim's positions are never read from the shared DB.

### 2. Sanity Checks on Close Records

**File:** `core/sim_bot.py` → `_log_closed_trade()`

Added detection for suspicious close records:

```python
if direction not in ("Buy", "Sell") or entry_score == 0:
    # Log warning with full context
    logger.warning(f"Suspicious close record: symbol={symbol} direction={direction} score={entry_score} reason={reason}")
    logger.warning(f"Current sim positions (in-memory): {state.positions}")
    logger.warning(f"Current sim positions (storage): {stored.get('positions')}")
```

When a close record has:
- `direction == "Unknown"` (not "Buy" or "Sell"), or
- `entry_score == 0` (no confidence in the entry)

The warning is logged with a complete snapshot of the in-memory and stored positions to enable forensic tracing.

### 3. Account Isolation Guarantee

**Verification:** Live `p_bot` account positions cannot bleed into the sim ledger

Implementation:
- `load_account()` ignores the shared DB when JSON file is present
- On load with missing JSON, any DB orphans are detected and logged but **not imported**
- New test case `test_sim_account_isolation()` verifies this guarantee

## Testing

Added three new test cases in `tests/test_sim_bot_logic.py`:

1. **`test_sim_bot_state_load_save()`** (updated)
   - Verifies JSON persistence works correctly
   - Ensures monkeypatch of PAPER_ACCOUNT_FILE is isolated per test

2. **`test_log_closed_trade_warning()`**
   - Confirms warning is logged when direction is invalid or score is 0
   - Validates that in-memory position snapshot is included

3. **`test_sim_account_isolation()`**
   - Writes a "live" position to the shared DB
   - Confirms `sim_bot` loads from JSON only, not from DB
   - Verifies warning is logged about ignored DB positions
   - Confirms legitimate sim positions persist to JSON and are reloaded correctly

**Test Results:**
```
tests/test_sim_bot_logic.py ........  [100%]
8 passed in 1.74s ✅
```

## Migration Notes

### For Existing Deployments

1. The first time `sim_bot` starts after this fix:
   - It will detect the absence of `paper_account.json`
   - If the shared DB contains orphaned positions, a warning will be logged
   - The sim will start fresh with `INITIAL_BALANCE`

2. To migrate existing sim state:
   - Manually export the current balance and positions from the DB
   - Create a `data/state/paper_account.json` file with the JSON payload
   - Restart `sim_bot`

### For New Deployments

- No action needed; JSON storage is the default

## Log Examples

### Warning: Suspicious Close Record
```
WARNING: Suspicious close record: symbol=BTCUSDT direction=Unknown score=0 reason=test
WARNING: Current sim positions (in-memory): [...]
WARNING: Current sim positions (storage): [...]
```

### Warning: DB Bleed Detection
```
WARNING: Shared DB contains positions ([...]) but JSON file is missing – ignoring to avoid bleed-over from live bot.
```

## References

- **Lock Hierarchy:** REF: [Tier 1] Lock Hierarchy (file_io_lock -> lock)
- **Related Issue:** Ghost trade / entry-exit reconciliation
- **Architecture:** See `docs/SYSTEM_ARCHITECTURE.md` for broader design context

## Verification Checklist

- ✅ Sim account loads from JSON file (primary source)
- ✅ Sim account never loads from shared DB when JSON is present
- ✅ Warnings logged when JSON is missing but DB contains positions
- ✅ Close records with bad direction/score generate warnings
- ✅ All sanctions and forensic info included in warnings
- ✅ Tests pass for isolation and warning logging
- ✅ Live `p_bot` cannot inject positions into sim ledger
