# 🐍 The Pythonic Oracle: Diagnostic Audit Report

## 1. PROJECT SCORECARD

* **Total Points:** 29
* **Final Severity:** 🔴 CRITICAL
* **Timestamp:** 2026-03-08 19:15 UTC

---

## 2. THE TIERED AUDIT LOG

### [sim_bot.py]

🔴 **Tier 1: NameError (Undefined Variable `fee`)**
* 🧐 **Explanation:** In `execute_sim_setup()`, the variable `fee` is used to decrement the balance (line 1201) but is never defined or calculated within the function scope.
* 💥 **Impact:** Immediate crash (`NameError`) every time the bot attempts to open a simulated position.
* 🛠 **Resolution:** Define `fee = notional * TAKER_FEE_RATE` prior to use.

🔴 **Tier 1: NameError (Undefined Variable `symbol`)**
* 🧐 **Explanation:** In `on_scan_result()`, the code checks `if symbol in state.fast_track_opened:` (line 1324), but `symbol` is not defined in this scope. The identifier used in the rest of the function is `r['inst_id']`.
* 💥 **Impact:** Immediate crash when a signal qualifies for fast-track entry, preventing any automated trading.
* 🛠 **Resolution:** Replace `symbol` with `r['inst_id']` or assign `symbol = r['inst_id']` at the start of the function.

🔴 **Tier 1: Undefined Global `LAST_EXIT_SCAN_TIME`**
* 🧐 **Explanation:** `check_opposite_signal()` declares `global LAST_EXIT_SCAN_TIME` (line 1047), but this variable is not initialized in the module's global scope.
* 💥 **Impact:** `NameError` on the first attempt to check for an exit signal, preventing the bot from correctly identifying reversal-based exit conditions.
* 🛠 **Resolution:** Initialize `LAST_EXIT_SCAN_TIME: Dict[str, float] = {}` at the module level.

---

### [p_bot.py]

🔴 **Tier 1: Critical Import Failure Escalation**
* 🧐 **Explanation:** The script uses `sys.exit(1)` (line 120) if `phemex_long` or `phemex_short` cannot be imported. While explicit, this is a hard failure in a module that might be imported by other tools (like `sim_bot.py`).
* 💥 **Impact:** Prevents the system from starting even if only partial functionality is needed.
* 🛠 **Resolution:** Use a try-except block to set a flag (e.g., `_SCANNERS_OK = False`) and gate execution logic rather than terminating the process.

🔵 **Tier 3: Non-Descriptive Variable Naming**
* 🧐 **Explanation:** Frequent use of short-hand variables like `nb`, `np` (line 527), `r1`, `r2` (line 893).
* 💥 **Impact:** Increases cognitive load for maintainers and violates PEP 8's preference for descriptive naming in complex logic.
* 🛠 **Resolution:** Rename to `new_balance`, `new_positions`, `active_order_resp`, and `conditional_order_resp`.

---

### [backtest.py]

🟡 **Tier 2: Statistical Instability (Zero Division)**
* 🧐 **Explanation:** `compute_sharpe` and `compute_sortino` (lines 1150, 1168) return 0.0 if standard deviation is 0, but `compute_sortino` can return `float("inf")` if no negative trades exist.
* 💥 **Impact:** Numerical instability in parameter optimization (`param_optimizer.py`) which depends on these metrics for composite scoring.
* 🛠 **Resolution:** Add a small epsilon to denominators or use `np.nan` to signify undefined results.

---

### [phemex_common.py]

🟡 **Tier 2: Brittle Rate Limit Recovery**
* 🧐 **Explanation:** `safe_request()` (line 280) handles 429 errors by sleeping and retrying exactly once.
* 💥 **Impact:** If the exchange-provided `Retry-After` is insufficient or multiple threads hit the limit simultaneously, the second attempt will fail and return `None`, potentially dropping critical data.
* 🛠 **Resolution:** Implement an exponential backoff loop with a configurable maximum retry count (e.g., 3-5 attempts).

---

### [ui.py]

🔵 **Tier 3: Missing Documentation**
* 🧐 **Explanation:** Core UI primitives like `hr_double`, `score_gauge`, and `sparkline` lack docstrings explaining their parameters and return types.
* 💥 **Impact:** Harder for developers to integrate the UI kit into new modules correctly.
* 🛠 **Resolution:** Add PEP 257 compliant docstrings to all exported functions.

---

### [Across Codebase]

🔵 **Tier 3: Temporal Inconsistency**
* 🧐 **Explanation:** Mix of `datetime.datetime.now()` and `datetime.datetime.utcnow()` across different modules for timestamping.
* 💥 **Impact:** Potential for "off-by-X-hours" bugs when comparing scan results to trade logs across different server timezones.
* 🛠 **Resolution:** Standardize on timezone-aware UTC objects: `datetime.datetime.now(datetime.timezone.utc)`.

---

## 3. EXECUTIVE SUMMARY
The FancyBot codebase exhibits high technical debt characterized by several "Critical" (Tier 1) NameErrors that will cause the simulation and live bots to crash immediately upon signal detection. While the architectural separation of concerns (common, scanners, risk) is sound, the lack of static analysis during the "AI-Upgrade" phase has introduced fatal logic gaps. **The most urgent priority is resolving the undefined variables in `sim_bot.py` and `p_bot.py` to achieve basic functional stability.**
