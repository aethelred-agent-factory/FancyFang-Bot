#!/usr/bin/env python3
import threading
import time
import datetime
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional

app = FastAPI(title="FancyBot Web Bridge")

# CORS for React frontend (localhost:5173 or localhost:3000)
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

def inject_state(state, logs):
    global _bot_state, _bot_logs
    _bot_state = state
    _bot_logs = logs

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
                upnl = (now - p['entry']) * p['size'] if p['side'] == "Buy" else (p['entry'] - now) * p['size']
                total_upnl += upnl

        return {
            "balance": round(_bot_state.balance, 2),
            "upnl": round(total_upnl, 4),
            "locked_margin": round(locked_margin, 2),
            "equity": round(_bot_state.balance + locked_margin + total_upnl, 2),
            "entropy": getattr(_bot_state, 'entropy_penalty', 0.0),
            "stats": {
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / total * 100) if total > 0 else 0, 1),
                "profit_factor": round((win_pnl / abs(loss_pnl)) if loss_pnl != 0 else 0, 2),
                "total_pnl": round(win_pnl + loss_pnl, 4)
            }
        }

@app.get("/api/positions")
async def get_positions():
    if not _bot_state:
        return []
    
    with _bot_state.lock:
        pos_list = []
        for p in _bot_state.positions:
            now = _bot_state.live_prices.get(p["symbol"], p["entry"])
            upnl = (now - p['entry']) * p['size'] if p['side'] == "Buy" else (p['entry'] - now) * p['size']
            pos_list.append({
                "symbol": p["symbol"],
                "side": p["side"],
                "entry": p["entry"],
                "current": now,
                "size": p["size"],
                "margin": p["margin"],
                "pnl": round(upnl, 4),
                "stop_price": p.get("stop_price", p["entry"])
            })
        return pos_list

@app.get("/api/logs")
async def get_logs():
    return list(_bot_logs)[-50:]

def run_bridge(host="0.0.0.0", port=8000):
    uvicorn.run(app, host=host, port=port, log_level="warning")

def start_bridge_thread(state, logs, port=8000):
    inject_state(state, logs)
    t = threading.Thread(target=run_bridge, kwargs={"port": port}, daemon=True)
    t.start()
    return t
