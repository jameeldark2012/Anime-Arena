from __future__ import annotations

import asyncio
import gc
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.debug import debug_event
from services.media.media_service import download_clip

_WHISPER_MODEL = None
_WHISPER_MODEL_NAME: str | None = None
# Track whether the previous turn used Ollama (local model)
# If True, Whisper should be unloaded after the turn completes
# If False (Gemini was used), Whisper stays loaded
_last_turn_used_ollama = False


def set_last_turn_used_ollama(value: bool) -> None:
    """Record whether the previous turn used Ollama (local model)."""
    global _last_turn_used_ollama
    _last_turn_used_ollama = value


def should_unload_whisper_model() -> bool:
    """Check if Whisper should be unloaded based on whether Ollama was used last turn."""
    global _last_turn_used_ollama
    return _last_turn_used_ollama


async def prepare_opponent_media(
    *,
    actions: list[dict],
    match_id: int,
    turn: int,
) -> list[dict]:
    try:
        return await _prepare_opponent_media(
            actions=actions,
            match_id=match_id,
            turn=turn,
        )
    finally:
        # Only unload if Ollama was used last turn
        if should_unload_whisper_model():
            unload_whisper_model()


async def _prepare_opponent_media(
    *,
    actions: list[dict],
    match_id: int,
    turn: int,
) -> list[dict]:
    """Download all opponent clips for one combined decision request."""
    media: list[dict] = []
    attached_actions = [action for action in actions if action.get("attachment_url")]
    debug_event(
        "opponent_video_analysis_input",
        match_id=match_id,
        turn=turn,
        action_count=len(actions),
        attached_action_count=len(attached_actions),
    )

    # First, download all clips concurrently
    download_tasks = []
    for action in actions:
        url = action.get("attachment_url")
        if not url:
            continue
        filename = str(action.get("filename") or "opponent_clip.mp4")
        download_tasks.append((action, filename, url))

    # Download all clips concurrently
    downloaded_clips = await asyncio.gather(*[
        _download_single_clip(action, filename, url, match_id, turn)
        for action, filename, url in download_tasks
    ])

    # Process each clip as it becomes available
    for clip_data in downloaded_clips:
        if clip_data is None:
            continue

        temp_path, filename, action = clip_data
        transcript = await asyncio.to_thread(_transcribe_media, temp_path)
        translation_enabled = os.environ.get(
            "OPPONENT_TRANSCRIPT_TRANSLATION_ENABLED", "true"
        ).lower() in {"1", "true", "yes", "on"}
        debug_event(
            "opponent_transcript_ready_for_prompt",
            match_id=match_id,
            turn=turn,
            filename=filename,
            model=os.environ.get("OPPONENT_WHISPER_MODEL", "large-v3"),
            task="translate" if translation_enabled else "transcribe",
            transcript_characters=len(transcript),
            transcript_preview=transcript[:1200],
            transcript_truncated=len(transcript) > 1200,
        )
        media.append({
            "action": action,
            "filename": filename,
            "path": temp_path,
            "transcript": transcript,
        })
        debug_event(
            "opponent_video_ready_for_combined_request",
            match_id=match_id,
            turn=turn,
            filename=filename,
            bytes=temp_path.stat().st_size if temp_path.exists() else 0,
        )

    debug_event(
        "opponent_video_analysis_prepared",
        match_id=match_id,
        turn=turn,
        prepared_media_count=len(media),
    )
    return media


async def _download_single_clip(
    action: dict,
    filename: str,
    url: str,
    match_id: int,
    turn: int,
) -> tuple[Path, str, dict] | None:
    """Download a single clip and return (temp_path, filename, action) or None on failure."""
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
        return None

    suffix = Path(filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_file.write(clip_bytes)
        temp_path = Path(temp_file.name)

    return temp_path, filename, action


def _get_whisper_model():
    global _WHISPER_MODEL, _WHISPER_MODEL_NAME
    from faster_whisper import WhisperModel

    model_name = os.environ.get("OPPONENT_WHISPER_MODEL", "large-v3")
    if _WHISPER_MODEL is None or _WHISPER_MODEL_NAME != model_name:
        unload_whisper_model()
        _WHISPER_MODEL = WhisperModel(model_name, device="cpu", compute_type="int8")
        _WHISPER_MODEL_NAME = model_name
        debug_event("opponent_speech_to_text_model_loaded", model=model_name)
    return _WHISPER_MODEL


def unload_whisper_model() -> None:
    """Drop the cached Whisper model and collect its native allocations."""
    global _WHISPER_MODEL, _WHISPER_MODEL_NAME
    model_name = _WHISPER_MODEL_NAME
    _WHISPER_MODEL = None
    _WHISPER_MODEL_NAME = None
    gc.collect()
    if model_name:
        debug_event("opponent_speech_to_text_model_unloaded", model=model_name)


async def warm_whisper_model() -> bool:
    """Load Whisper after a decision so it is ready for the next turn.
    
    Only warm if Ollama was NOT used last turn (i.e., Gemini was used).
    If Ollama was used, Whisper was just unloaded and should stay unloaded.
    
    Returns True if Whisper was loaded, False otherwise.
    """
    if os.environ.get("OPPONENT_STT_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return False
    # Only warm if Ollama was NOT used last turn
    if not should_unload_whisper_model():
        try:
            _get_whisper_model()
            return True
        except ImportError:
            debug_event("opponent_speech_to_text_skipped", reason="faster_whisper_not_installed")
        except Exception as exc:
            debug_event(
                "opponent_speech_to_text_model_warm_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
    return False


def _transcribe_media(media_path: Path) -> str:
    """Extract speech locally with Whisper without making STT an AI-provider call."""
    if os.environ.get("OPPONENT_STT_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return ""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        debug_event("opponent_speech_to_text_skipped", reason="ffmpeg_not_found")
        return ""

    model_name = os.environ.get("OPPONENT_WHISPER_MODEL", "large-v3")
    translation_enabled = os.environ.get(
        "OPPONENT_TRANSCRIPT_TRANSLATION_ENABLED", "true"
    ).lower() in {"1", "true", "yes", "on"}
    task = "translate" if translation_enabled else "transcribe"
    try:
        model = _get_whisper_model()
    except ImportError:
        debug_event("opponent_speech_to_text_skipped", reason="faster_whisper_not_installed")
        return ""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
        audio_path = Path(audio_file.name)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(media_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )
        segments, _ = model.transcribe(str(audio_path), vad_filter=True, task=task)
        transcript = " ".join(segment.text.strip() for segment in segments).strip()
        debug_event(
            "opponent_speech_to_text_complete",
            filename=media_path.name,
            transcript_characters=len(transcript),
            model=model_name,
            task=task,
        )
        return transcript
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        debug_event(
            "opponent_speech_to_text_failed",
            filename=media_path.name,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return ""
    finally:
        audio_path.unlink(missing_ok=True)


def cleanup_opponent_media(media: list[dict]) -> None:
    """Delete temporary opponent files after the combined request completes."""
    for item in media:
        path = item.get("path")
        if isinstance(path, Path):
            path.unlink(missing_ok=True)
