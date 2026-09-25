from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)


async def download_clip(url: str) -> bytes | None:
    """Download a clip attachment from a public URL."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        logger.exception("Failed to download attachment: %s", url)
    return None


def probe_video_codec(data: bytes) -> str | None:
    """Probe a clip's codec and return the codec name if available."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_streams",
                    tmp_path,
                ],
                capture_output=True,
                timeout=10,
            )
            streams = json.loads(result.stdout).get("streams", [])
            for stream in streams:
                if stream.get("codec_type") == "video":
                    return stream.get("codec_name")
        finally:
            os.unlink(tmp_path)
    except Exception:
        logger.exception("ffprobe check failed.")
    return None


async def validate_clip_upload(url: str) -> tuple[bytes | None, str | None]:
    """Return (clip_bytes, codec_name)."""
    clip_bytes = await download_clip(url)
    if clip_bytes is None:
        return None, None

    loop = asyncio.get_running_loop()
    codec = await loop.run_in_executor(None, probe_video_codec, clip_bytes)
    return clip_bytes, codec


def is_supported_video_extension(filename: str) -> bool:
    """Return True for the video extensions we support for battle uploads."""
    return filename.lower().endswith(('.mp4', '.mov', '.webm', '.mkv'))


def probe_video_duration(video_path: Path) -> float:
    """Return the duration of a video file in seconds using ffprobe, or 0.0 on failure."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return 0.0
    if completed.returncode != 0:
        return 0.0
    output = completed.stdout.strip()
    if not output:
        return 0.0
    try:
        return float(output.splitlines()[0])
    except ValueError:
        return 0.0


SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv",
}


def collect_videos(root: str | Path) -> list[Path]:
    """Recursively collect all supported video files under root, sorted by path."""
    base = Path(root).expanduser().resolve()
    if not base.exists():
        raise FileNotFoundError(f"video root does not exist: {base}")
    return [
        path
        for path in sorted(base.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
    ]


def estimate_video_tokens(duration_seconds: float) -> int:
    """Estimate Gemini token cost for a video based on its duration.

    Gemini charges roughly 1200 tokens per second of video.
    Returns a minimum of 4000 tokens for very short or unknown-duration clips.
    """
    if duration_seconds <= 0:
        return 4000
    return max(4000, int(duration_seconds * 1200))


async def validate_h264_clip(url: str, *, filename: str | None = None) -> tuple[bytes | None, str | None]:
    """Return (clip_bytes, codec_name) for non-H.264 clips only.

    A valid H.264 upload returns (clip_bytes, None). A non-H.264 upload returns
    (clip_bytes, codec_name), which the caller can reject with a friendly
    conversion message.
    """
    if filename is not None and not is_supported_video_extension(filename):
        return None, "unsupported_extension"

    clip_bytes, codec = await validate_clip_upload(url)
    if clip_bytes is None:
        return None, None
    if codec is None or codec.lower() == "h264":
        return clip_bytes, None
    return clip_bytes, codec
