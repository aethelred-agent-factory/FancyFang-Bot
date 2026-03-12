#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import subprocess
import threading

import research.strategy_analyzer as analyzer
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="FancyBot Web Bridge")

# Get path to web directory
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

# CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global bot state and logs (to be injected)
_bot_state = None
_bot_logs = []

# storage helper used by VoltAgent endpoints; instantiate lazily
from modules.storage_manager import StorageManager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "state" / "fancybot.db"
_storage = StorageManager(DB_PATH)

# Pydantic models for VoltAgent payloads
from typing import List, Optional
from pydantic import BaseModel

class AnnotationPayload(BaseModel):
    trade_id: int
    narrative: str
    tags: List[str]
    primary_driver: Optional[str]
    failure_mode: Optional[str]

class JournalPayload(BaseModel):
    entry: str


def inject_state(state, logs):
    global _bot_state, _bot_logs
    _bot_state = state
    _bot_logs = logs


@app.get("/")
async def get_index():
    """Serve the main dashboard HTML."""
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


# Mount static files if needed (for CSS/JS in subfolders)
if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def _max_positions_from_state():
    """Default max positions when not provided by state."""
    if not _bot_state:
        return 8
    # Optional: get_dynamic_max_positions(balance) if available
    return getattr(_bot_state, "max_positions", 8)


@app.get("/trade/{trade_id}")
async def get_trade(trade_id: int):
    """VoltAgent tool: fetch a closed trade record by ID"""
    return _storage.get_trade_by_id(trade_id)


@app.post("/trade/annotate")
async def annotate_trade(payload: AnnotationPayload):
    """VoltAgent tool: write DeepSeek annotation back to the trade record"""
    _storage.update_trade_narration(
        payload.trade_id,
        {
            "narrative": payload.narrative,
            "tags": payload.tags,
            "primary_driver": payload.primary_driver,
            "failure_mode": payload.failure_mode,
        },
    )
    return {"status": "ok"}


@app.get("/market_context/latest")
async def get_market_context():
    """VoltAgent tool: return the most recent market context snapshot"""
    # reuse the existing market context manager if possible
    from modules.market_context import market_ctx_manager
    return market_ctx_manager.get_market_context_snapshot()


@app.get("/failure_history")
async def get_failure_history():
    """VoltAgent tool: return failure mode distribution"""
    return _storage.get_failure_mode_distribution()


@app.get("/candidate/{symbol}")
async def get_candidate_features(symbol: str):
    """VoltAgent tool: fetch latest ml_features for a given symbol"""
    return _storage.get_latest_features(symbol)


@app.post("/journal/append")
async def append_journal(payload: JournalPayload):
    """VoltAgent tool: append a caretakers log entry"""
    with open('docs/AI_CARETAKERS_JOURNAL.md', 'a') as f:
        f.write(payload.entry)
    return {'status': 'ok'}


@app.get("/api/summary")
async def get_summary():
    if not _bot_state:
        return {"error": "Bot state not connected"}

    with _bot_state.lock:
        wins = _bot_state.rolling_stats["wins"]
        losses = _bot_state.rolling_stats["losses"]
        total = wins + losses
        win_pnl = _bot_state.rolling_stats["win_pnl"]
        loss_pnl = _bot_state.rolling_stats["loss_pnl"]

        # Calculate uPnL
        total_upnl = 0.0
        locked_margin = 0.0
        for p in _bot_state.positions:
            locked_margin += p.get("margin", 0.0)
            now = _bot_state.live_prices.get(p["symbol"])
            if now:
                upnl = (
                    (now - p["entry"]) * p["size"]
                    if p["side"] == "Buy"
                    else (p["entry"] - now) * p["size"]
                )
                total_upnl += upnl

        equity = round(_bot_state.balance + locked_margin + total_upnl, 2)
        balance = round(_bot_state.balance, 2)
        total_pnl = round(win_pnl + loss_pnl, 4)
        win_rate = round((wins / total * 100) if total > 0 else 0, 1)
        avg_win = round(win_pnl / wins, 2) if wins > 0 else 0.0
        avg_loss = round(loss_pnl / losses, 2) if losses > 0 else 0.0
        # Account health: simple % of balance that is "safe" (equity vs balance)
        account_health = round((equity / balance * 100) if balance > 0 else 0, 2)
        n_pos = len(_bot_state.positions)
        max_pos = _max_positions_from_state()

        return {
            "balance": balance,
            "upnl": round(total_upnl, 4),
            "open_pnl": round(total_upnl, 2),
            "day_pnl": total_pnl,
            "locked_margin": round(locked_margin, 2),
            "equity": equity,
            "entropy": round(getattr(_bot_state, "entropy_penalty", 0.0), 3),
            "account_health": account_health,
            "position_load": n_pos,
            "max_positions": max_pos,
            "scans": getattr(_bot_state, "scan_count", 0),
            "analyzed": getattr(_bot_state, "analyzed_count", 0),
            "mode": "SIM",
            "version": "2.0",
            "running": getattr(_bot_state, "is_running", True),
            "stats": {
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "profit_factor": round(
                    (win_pnl / abs(loss_pnl)) if loss_pnl != 0 else 0, 2
                ),
                "total_pnl": total_pnl,
                "total_trades": total,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
            },
        }


@app.get("/api/positions")
async def get_positions():
    if not _bot_state:
        return []

    with _bot_state.lock:
        pos_list = []
        for p in _bot_state.positions:
            now = _bot_state.live_prices.get(p["symbol"], p["entry"])
            upnl = (
                (now - p["entry"]) * p["size"]
                if p["side"] == "Buy"
                else (p["entry"] - now) * p["size"]
            )
            entry = p["entry"]
            notional = entry * p["size"] if entry and p.get("size") else 0
            pnl_pct = (upnl / notional * 100) if notional else 0
            side = "Long" if p["side"] == "Buy" else "Short"
            stop = p.get("stop_price") or p.get("original_stop") or entry
            target = p.get("take_profit")
            pos_list.append(
                {
                    "symbol": p["symbol"],
                    "side": side,
                    "entry": entry,
                    "current": now,
                    "stop": stop,
                    "target": target,
                    "size": p.get("margin", p.get("size", 0)),
                    "margin": p.get("margin", 0),
                    "pnl": round(upnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "stop_price": stop,
                    "score": p.get("entry_score") or p.get("score") or 0,
                    "rsi": None,
                    "regime": None,
                    "kalman": None,
                    "tsl": bool(p.get("trail_dist")),
                }
            )
        return pos_list


@app.get("/api/logs")
async def get_logs():
    raw = list(_bot_logs)[-50:]
    # Normalize to [{time, type, msg}] for dashboard; accept strings from buffer
    out = []
    for line in raw:
        if isinstance(line, dict):
            out.append(line)
        else:
            s = str(line).strip()
            # Try to parse "HH:MM:SS" or "YYYY-MM-DD HH:MM:SS ..." or plain message
            time_part = ""
            msg_part = s
            if len(s) >= 8 and s[2] == ":" and s[5] == ":":
                time_part = s[:8]
                msg_part = s[9:].strip() if len(s) > 8 else s
            elif " " in s:
                parts = s.split(" ", 2)
                if len(parts) >= 3 and ":" in parts[1]:
                    time_part = parts[1]
                    msg_part = parts[2]
            typ = "SYSTEM"
            if "FAIL" in msg_part or "ERROR" in msg_part:
                typ = "ERROR"
            elif "SKIP" in msg_part or "THROTTLE" in msg_part or "WARN" in msg_part:
                typ = "WARN"
            elif (
                "CLOSED" in msg_part
                or "ENTRY" in msg_part
                or "ENTERED" in msg_part
                or "SCALED" in msg_part
            ):
                typ = "TRADE"
            elif "Scan" in msg_part or "scan" in msg_part or "Scored" in msg_part:
                typ = "SCAN"
            out.append({"time": time_part or "—", "type": typ, "msg": msg_part})
    return out


@app.get("/api/scanner")
async def get_scanner():
    """Scanner results; empty when not provided by bot state."""
    if not _bot_state:
        return []
    candidates = getattr(_bot_state, "last_scanner_results", None)
    if candidates is None:
        return []
    # Expect list of dicts with symbol, price, score, dir, rsi, etc.
    return list(candidates) if isinstance(candidates, list) else []


@app.get("/api/backtest/stats")
async def get_backtest_stats():
    """Returns analyzed stats from backtest_results.json."""
    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, "backtest_results.json")
    if not os.path.exists(path):
        return {
            "error": "backtest_results.json not found",
            "symbols": [],
            "signals": [],
        }

    stats = analyzer.analyze_results(path)
    return stats


@app.get("/api/backtest/optimizer")
async def get_optimizer_results():
    """Returns top results from optimizer_results.json."""
    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, "research", "optimizer_results.json")
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/backtest/run")
async def run_backtest():
    """Trigger a backtest run in the background."""
    root = os.path.dirname(os.path.dirname(__file__))
    script = os.path.join(root, "research", "backtest.py")
    # Run in background
    subprocess.Popen([sys.executable, script, "--candles", "300"], cwd=root)
    return {"status": "started"}


@app.post("/api/backtest/optimize")
async def run_optimize():
    """Trigger an optimizer run in the background."""
    root = os.path.dirname(os.path.dirname(__file__))
    script = os.path.join(root, "research", "param_optimizer.py")
    # Run in background
    subprocess.Popen([sys.executable, script], cwd=root)
    return {"status": "started"}


def run_bridge(host="0.0.0.0", port=8080):
    print("\n  🚀 FANCYBOT WEB SERVER ACTIVE")
    print(f"  🌐 INTERFACE: http://localhost:{port}")
    print(f"  📡 API BASE:  http://localhost:{port}/api\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


def start_bridge_thread(state, logs, port=8080):
    inject_state(state, logs)
    t = threading.Thread(target=run_bridge, kwargs={"port": port}, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    # Standalone test mode
    class MockState:
        def __init__(self):
            self.lock = threading.Lock()
            self.balance = 10000.0
            self.positions = []
            self.live_prices = {}
            self.rolling_stats = {
                "wins": 0,
                "losses": 0,
                "win_pnl": 0.0,
                "loss_pnl": 0.0,
            }

    _bot_state = MockState()
    run_bridge(port=8080)
