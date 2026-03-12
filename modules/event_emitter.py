"""Lightweight event emitter for VoltAgent coordination.

This module provides a simple `emit` function that posts to the
VoltAgent supervisor endpoint.  Most calls are fire-and-forget; only the
CANDIDATE_ENTRY event needs a synchronous response.
"""
import threading
import httpx
from typing import Any, Dict, Optional

VOLTAGENT_URL = "http://localhost:3141"
TIMEOUT = 5.0


def emit(event_type: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Send an event to the VoltAgent supervisor.

    Parameters
    ----------
    event_type : str
        One of TRADE_CLOSED, CANDIDATE_ENTRY, NEWS_TICK, etc.
    payload : dict
        Arbitrary JSON-serializable payload associated with the event.

    Returns
    -------
    dict or None
        For synchronous events (currently only CANDIDATE_ENTRY) the
        parsed JSON response is returned.  For others, None is returned;
        failures are swallowed so that VoltAgent downtime never blocks the
        trading bot.
    """
    url = f"{VOLTAGENT_URL}/api/agents/supervisor/generate"
    data = {"message": f"EVENT:{event_type}", "context": payload}

    try:
        # CANDIDATE_ENTRY needs the response to decide
        if event_type == "CANDIDATE_ENTRY":
            resp = httpx.post(url, json=data, timeout=TIMEOUT)
            return resp.json()
        else:
            # fire-and-forget in a thread
            def _fire():
                try:
                    httpx.post(url, json=data, timeout=TIMEOUT)
                except Exception:
                    pass

            threading.Thread(target=_fire, daemon=True).start()
    except Exception:
        # suppress all errors to avoid interrupting the bot
        return None
    return None
