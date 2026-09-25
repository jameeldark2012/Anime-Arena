from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def debug_event(event: str, **payload: Any) -> None:
    """Emit a readable, structured debug section while DEBUG is enabled."""
    from core.config import settings

    if not settings.DEBUG:
        return

    title = event.replace("_", " ").upper()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload_text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    separator = "=" * 88
    section = (
        f"\n{separator}\n"
        f"DEBUG EVENT: {title}\n"
        f"{separator}\n"
        f"Timestamp: {timestamp}\n"
        f"Event key: {event}\n"
        "Payload:\n"
        f"{payload_text}\n"
        f"{separator}\n"
    )
    print(section, end="", flush=True)
