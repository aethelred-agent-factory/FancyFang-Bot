# FancyBot Stability & Architectural Mandates
**CRITICAL: ALL AI DEVELOPERS MUST READ THIS BEFORE MODIFYING CODE.**

This document details two catastrophic failures that occurred during the March 2026 refactor. These incidents serve as a roadmap for avoiding system-wide stalls and thread deadlocks.

---

## 🛑 Incident 1: The "Silent Scan" (NameError Crash)
**Date:** 2026-03-08  
**Symptom:** Bot scans correctly but returns zero hits (`L: 0 S: 0`) even in high-volatility markets.

### 🔍 Root Cause
During a renaming pass to align with PEP 8 (changing `fr` to `funding_rate`), a single reference was missed in the final results dictionary in `phemex_common.unified_analyse`. 

Because this code ran inside a concurrent `ThreadPoolExecutor` with a broad `try-except` block, the `NameError: fr_change is not defined` did not crash the bot. Instead, it caused the `analyse()` function to return `None` for every single ticker, effectively blinding the bot.

### 🛠 The Fix
Synchronized all variable names and implemented `diag_scan.py` to verify core math independently of the TUI.

---

## 🛑 Incident 2: The "Deadlock Freeze" (Lock Ordering Failure)
**Date:** 2026-03-08  
**Symptom:** Trades are verified and approve slippage, but the bot freezes and never logs "ENTERED."

### 🔍 Root Cause
A classic **Lock Order Inversion**. 
1. The bot used two primary locks: `state.lock` (for memory) and `state.file_io_lock` (for disk).
2. Standard methods like `load_account()` acquired `file_io_lock` first, then `lock`.
3. `execute_sim_setup()` was holding `state.lock` and then calling `save_account()`, which attempted to acquire `file_io_lock`.
4. If two threads hit these at the same time, the bot deadlocked and stopped all execution.

### 🛠 The Fix
1.  Converted `state.lock` to a `threading.RLock` (Reentrant Lock) to prevent self-deadlocking.
2.  **Narrowed Scope:** Refactored `execute_sim_setup` to release `state.lock` before performing I/O or calling `save_account()`.
3.  **Hierarchy enforced:** `file_io_lock` must ALWAYS be the outer lock if both are needed.

---

## 📜 Mandatory Protocols for Future AI Developers

### 1. Verification is Non-Negotiable
Never assume a refactor works because it "looks" clean. 
- Run `python3 -m py_compile <file>` after every change.
- Run a standalone math-only script (like `diag_scan.py`) to ensure indicators are still calculating.

### 2. The Threading "Golden Rules"
- **Avoid Nested Locks:** If you must nest locks, you **must** use the same order everywhere in the codebase.
- **Narrow the Scope:** Hold a lock for the absolute minimum time required to copy or update data. Never perform I/O (file writes, API calls) or play animations while holding a lock.
- **Use RLock by Default:** When managing complex class states where methods might call each other, prefer `threading.RLock`.

### 3. Log your Failures
- **Never** use `except Exception: pass`.
- Always log the traceback using `traceback.format_exc()`. If a thread dies, it must die loudly.

### 4. Grep Before you Commit
If you rename a variable or a class attribute, use `grep_search` to verify **every single file** in the workspace. A single missed character in a dictionary key can stall the entire system.

**Signed,**
*Gemini CLI (Refactor Lead)*
