"""
convert_to_h264.py
------------------
Recursively converts all video files in a folder (and its subfolders) to
H.264 / AAC MP4 — the format with the broadest compatibility across Discord,
browsers, and mobile devices.

Usage:
    python -m scripts.convert_to_h264 <folder>

    # Dry run (shows what would be converted, touches nothing):
    python -m scripts.convert_to_h264 <folder> --dry-run

    # Delete originals after successful conversion (default: keep them):
    python -m scripts.convert_to_h264 <folder> --delete-originals

Requirements:
    pip install ffmpeg-python tqdm
    ffmpeg must be installed and available on PATH.
    Windows:  winget install ffmpeg   or   scoop install ffmpeg
    macOS:    brew install ffmpeg

Notes:
    - Files already encoded as H.264 are skipped (no re-encode).
    - Output files are written next to the originals with a _h264 suffix
      before the extension, then renamed over the original once confirmed good.
    - If a conversion fails the original is left untouched and the error is logged.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import ffmpeg
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}


def _probe(path: Path) -> tuple[str | None, bool]:
    """Return (video_codec, has_audio) for the file, or (None, False) on failure."""
    try:
        probe = ffmpeg.probe(str(path))
        streams = probe.get("streams", [])
        video_codec = None
        has_audio = False
        for stream in streams:
            if stream.get("codec_type") == "video" and video_codec is None:
                video_codec = stream.get("codec_name")
            if stream.get("codec_type") == "audio":
                has_audio = True
        return video_codec, has_audio
    except ffmpeg.Error:
        logger.warning("Could not probe %s — skipping.", path.name)
        return None, False


def _collect_videos(root: Path) -> list[Path]:
    """Recursively collect all video files under root."""
    return [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]


def _convert(src: Path, delete_original: bool, dry_run: bool) -> bool:
    """Convert src to H.264/AAC MP4 in-place.

    Returns True on success (or skip), False on failure.
    """
    codec, has_audio = _probe(src)
    if codec is None:
        return False

    if codec == "h264":
        logger.info("SKIP   already H.264: %s", src.name)
        return True

    tmp = src.with_stem(src.stem + "_h264_tmp").with_suffix(".mp4")

    if dry_run:
        logger.info("DRY    would convert (%s → h264, audio=%s): %s", codec, has_audio, src)
        return True

    logger.info("CONV   %s → h264: %s", codec, src)

    try:
        inp = ffmpeg.input(str(src))
        video = inp.video

        if has_audio:
            audio = inp.audio
            out = ffmpeg.output(
                video, audio, str(tmp),
                vcodec="libx264",
                acodec="aac",
                crf=18,
                preset="fast",
                movflags="+faststart",
                pix_fmt="yuv420p",
                profile="baseline",
                level="4.0",
                **{"b:a": "192k"},
            )
        else:
            # No audio track — add a silent one so Discord doesn't reject it.
            silent = ffmpeg.input("anullsrc=r=44100:cl=stereo", f="lavfi")
            out = ffmpeg.output(
                video, silent, str(tmp),
                vcodec="libx264",
                acodec="aac",
                crf=18,
                preset="fast",
                movflags="+faststart",
                pix_fmt="yuv420p",
                profile="baseline",
                level="4.0",
                **{"b:a": "192k"},
                shortest=None,  # end when video ends
            )

        out.overwrite_output().run(quiet=True)

    except ffmpeg.Error as e:
        logger.error("FAIL   %s\n%s", src.name, e.stderr.decode(errors="replace") if e.stderr else "")
        if tmp.exists():
            tmp.unlink()
        return False

    # Swap: rename original to .bak, move tmp to original name.
    bak = src.with_suffix(".bak")
    src.rename(bak)
    tmp.rename(src.with_suffix(".mp4"))

    if delete_original:
        bak.unlink()
        logger.info("DEL    original removed: %s", bak.name)
    else:
        logger.info("BAK    original kept as: %s", bak.name)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-convert videos to H.264 MP4.")
    parser.add_argument("folder", type=Path, help="Root folder to scan recursively.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be converted without doing anything.",
    )
    parser.add_argument(
        "--delete-originals", action="store_true",
        help="Delete original files after successful conversion.",
    )
    args = parser.parse_args()

    root: Path = args.folder.resolve()
    if not root.is_dir():
        logger.error("Not a directory: %s", root)
        sys.exit(1)

    # Check ffmpeg is available.
    if shutil.which("ffmpeg") is None:
        logger.error(
            "ffmpeg not found on PATH.\n"
            "  Windows: winget install ffmpeg\n"
            "  macOS:   brew install ffmpeg"
        )
        sys.exit(1)

    videos = _collect_videos(root)
    if not videos:
        logger.info("No video files found under %s", root)
        return

    logger.info("Found %d video file(s) under %s", len(videos), root)
    if args.dry_run:
        logger.info("DRY RUN — no files will be modified.")

    ok = fail = skip = 0
    for path in tqdm(videos, desc="Converting", unit="file"):
        result = _convert(path, delete_original=args.delete_originals, dry_run=args.dry_run)
        if result:
            ok += 1
        else:
            fail += 1

    logger.info("Done. converted=%d  failed=%d  (dry_run=%s)", ok, fail, args.dry_run)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
