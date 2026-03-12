import json
import time
import uuid
from pathlib import Path

_LOG_PATH = Path("debug-3b23fa.log")
_SESSION_ID = "3b23fa"


def dbg_log(
    *,
    hypothesisId: str,
    location: str,
    message: str,
    data: dict | None = None,
    runId: str = "pre-fix",
) -> None:
    try:
        payload = {
            "sessionId": _SESSION_ID,
            "id": f"log_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data or {},
            "runId": runId,
            "hypothesisId": hypothesisId,
        }
        _LOG_PATH.open("a", encoding="utf-8").write(
            json.dumps(payload, ensure_ascii=False) + "\n"
        )
    except Exception:
        pass
