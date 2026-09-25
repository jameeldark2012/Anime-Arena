from __future__ import annotations

import tempfile
from pathlib import Path

from core.debug import debug_event
from services.media.media_service import download_clip


async def prepare_opponent_media(
    *,
    actions: list[dict],
    match_id: int,
    turn: int,
) -> list[dict]:
    """Download all opponent clips for one combined decision request."""
    media: list[dict] = []
    for action in actions:
        url = action.get("attachment_url")
        if not url:
            continue

        filename = str(action.get("filename") or "opponent_clip.mp4")
        debug_event(
            "opponent_video_download_started",
            match_id=match_id,
            turn=turn,
            filename=filename,
        )
        clip_bytes = await download_clip(url)
        if clip_bytes is None:
            debug_event(
                "opponent_video_download_failed",
                match_id=match_id,
                turn=turn,
                filename=filename,
            )
            continue

        suffix = Path(filename).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(clip_bytes)
            temp_path = Path(temp_file.name)

        media.append({"action": action, "filename": filename, "path": temp_path})
        debug_event(
            "opponent_video_ready_for_combined_request",
            match_id=match_id,
            turn=turn,
            filename=filename,
            bytes=len(clip_bytes),
        )

    return media


def cleanup_opponent_media(media: list[dict]) -> None:
    """Delete temporary opponent files after the combined request completes."""
    for item in media:
        path = item.get("path")
        if isinstance(path, Path):
            path.unlink(missing_ok=True)
