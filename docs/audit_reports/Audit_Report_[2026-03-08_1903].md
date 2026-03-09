# Architectural Audit Report [2026-03-08_1903]

## 📊 PROJECT SCORECARD

| Metric | Value |
| --- | --- |
| **Total Points** | **58 pts** |
| **Severity Band** | **🔴 HIGH** |
| **Target Severity** | **0 pts** |

---

## 📋 TIERED AUDIT LOG

### 🔴 Tier 1: Critical (5 pts each)
1. **[Lock Order Inversion]** in `sim_bot.py`: Potential deadlock between `file_io_lock` and `lock`. `save_account` is called while logic elsewhere might nest these in reverse.
   - 💥 **Impact:** Complete system freeze during trade execution.
2. **[Potential NameError]** in `p_bot.py` and `sim_bot.py`: Use of terse variables in complex logic increases risk of `NameError` after refactors (similar to Incident 1 in `MUST_READ`).
   - 💥 **Impact:** Silent failure of scanners or trade execution.
3. **[Missing Thread Tracebacks]** in background workers (`_ws_heartbeat`, `_cache_refresher`): Errors inside these threads are not consistently logged with full tracebacks.
   - 💥 **Impact:** Background processes die silently, stopping live price updates.

### 🟡 Tier 2: Logic (3 pts each)
1. **[UTC Non-Compliance]** in `phemex_common.py`, `p_bot.py`, `sim_bot.py`: Use of `datetime.datetime.now()` instead of timezone-aware UTC.
   - 💥 **Impact:** Inaccurate trade durations and potential issues with midnight-reseting drawdown guards.
2. **[Redundant I/O under Lock]** in `sim_bot.py`: File writing (`save_account`) occurs while holding or immediately following state locks, blocking other threads.
   - 💥 **Impact:** Performance degradation and increased contention.
3. **[O(n^2) Loop Risk]** in `p_bot.py` candidate picking: Multiple iterations over results for filtering and sorting.
   - 💥 **Impact:** High CPU usage during high-volatility events with many signals.
4. **[Unsafe Shared State Access]** in `p_bot.py` (`_live_prices`): Updates and reads are not always protected by a consistent lock across all modules.
   - 💥 **Impact:** Race conditions in PnL and stop-loss calculations.
5. **[Brittle Rate Limit Recovery]** in `phemex_common.py`: Global backoff is set but not always respected by all concurrent threads immediately.
   - 💥 **Impact:** Successive 429 errors and potential API ban.

### 🔵 Tier 3: Style (1 pt each)
1. **[Terse Naming]** (e.g., `nb`, `np`, `r`, `t`, `l`, `ch`): Pervasive across the codebase.
2. **[Missing Docstrings]** in many helper functions.
3. **[PEP 8 Violations]** (long lines, inconsistent spacing).
4. **[Inconsistent Log Formats]** between modules.

---

## 🗺️ LOCK HIERARCHY MAP

| Lock Level | Name | Responsibility |
| --- | --- | --- |
| **Level 1 (Outer)** | `file_io_lock` | Protects disk operations (`paper_account.json`, logs). |
| **Level 2 (Inner)** | `lock` / `_lock` | Protects in-memory state (balances, positions, prices). |

**Violations Detected:**
- In `sim_bot.py`, the hierarchy is sometimes ambiguous, especially when `save_account` is called from within complex state-modifying functions.

---

## 🛠️ REVISION DIRECTIVE SUMMARY
1. **Standardize UTC:** Replace all `datetime.now()` with `datetime.now(datetime.timezone.utc)`.
2. **Enforce Lock Order:** Ensure `file_io_lock` is always acquired before `lock` if both are needed.
3. **Refactor Naming:** Rename all terse variables to descriptive ones.
4. **Add Error Handling:** Wrap all thread `target` functions in `try...except` with `traceback.format_exc()`.
5. **Ruff Alignment:** Apply linting-based refactoring.
