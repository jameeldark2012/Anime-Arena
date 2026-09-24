from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def debug_event(event: str, **payload: Any) -> None:
    """Emit a minimal structured debug event while DEBUG mode is enabled."""
    from core.config import settings

    if not settings.DEBUG:
        return

    entry = {
        "ts": datetime.utcnow().isoformat(timespec="seconds"),
        "event": event,
        "payload": payload,
    }
    print(json.dumps(entry, default=str))
