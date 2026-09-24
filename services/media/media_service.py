from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile

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
