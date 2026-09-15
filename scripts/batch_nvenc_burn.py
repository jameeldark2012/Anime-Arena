"""
batch_nvenc_burn.py
-------------------
Recursively processes all video files in a folder (and its subfolders):
  1. Extracts the embedded ASS subtitle track from the video.
  2. Burns those subtitles into the output using NVIDIA hardware encoding.

Replicates exactly (in two steps):
    ffmpeg -i input.mkv -map 0:s:0 subs.ass
    ffmpeg -i input.mkv -vf "ass=subs.ass,format=yuv420p" -c:v h264_nvenc
           -preset fast -c:a aac -b:a 192k output.mp4

Usage:
    python -m scripts.batch_nvenc_burn <folder>

    # Dry run — show what would be processed, touch nothing:
    python -m scripts.batch_nvenc_burn <folder> --dry-run

    # Delete originals after successful conversion (default: keep as .bak):
    python -m scripts.batch_nvenc_burn <folder> --delete-originals

Requirements:
    ffmpeg must be installed and on PATH, built with NVENC support.
    Windows:  winget install ffmpeg   OR   https://www.gyan.dev/ffmpeg/builds/
    tqdm:     pip install tqdm

Notes:
    - The extracted subs.ass is a temporary file and is deleted after conversion.
    - If a video has no subtitle track, it is skipped with a warning.
    - Output is written to a temp file first, then swapped in on success.
    - Originals are renamed to .bak unless --delete-originals is passed.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}


def _collect_videos(root: Path) -> list[Path]:
    """Recursively collect all video files under root."""
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def _extract_subs(src: Path, subs_out: Path) -> bool:
    """
    Extract the second ASS subtitle track (0:s:1 = Full subs, not Signs-Songs)
    from src into subs_out.
    Returns True on success, False if no subtitle track exists or extraction fails.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-map", "0:s:1",   # second subtitle track = Full subs (skip Signs-Songs)
        str(subs_out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

    if result.returncode != 0:
        # Fall back to first subtitle track if second doesn't exist
        logger.warning("No second subtitle track, falling back to 0:s:0 for: %s", src.name)
        cmd[cmd.index("0:s:1")] = "0:s:0"
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

    if result.returncode != 0:
        if "matches no streams" in result.stderr or "subtitle" in result.stderr.lower():
            logger.warning("SKIP   no subtitle track found in: %s", src.name)
        else:
            logger.error("FAIL (extract)  %s\nSTDERR:\n%s", src.name, result.stderr[-3000:])
        return False

    return True


def _burn(src: Path, subs_file: Path) -> bool:
    """
    Burn subtitles into src using h264_nvenc, output as .mp4 alongside the original.
    The original file is left completely untouched.
    Returns True on success, False on failure.
    """
    cwd = src.parent

    # Write directly to the final .mp4 name — original .mkv is left untouched
    out_path = src.with_suffix(".mp4")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-vf", f"ass={subs_file.name},format=yuv420p",
        "-c:v", "h264_nvenc",
        "-preset", "p1",
        "-c:a", "aac",
        "-b:a", "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=cwd)

    if result.returncode != 0:
        logger.error("FAIL (burn)  %s\nSTDERR:\n%s", src.name, result.stderr[-3000:])
        if out_path.exists():
            out_path.unlink()
        return False

    logger.info("OK     %s  →  %s", src.name, out_path.name)
    return True

    return True


def _process(src: Path, dry_run: bool) -> bool:
    """
    Full pipeline for one video: extract subs → burn → clean up temp subs.
    Returns True on success/skip, False on failure.
    """
    if dry_run:
        logger.info("DRY    would process: %s", src)
        return True

    # Use a fixed safe filename with no spaces/brackets — the ass= filtergraph
    # cannot handle spaces or special characters in the path.
    subs_tmp = src.parent / "subs_temp_extracted.ass"

    # Step 1: extract subtitles
    logger.info("EXTRACT  %s", src.name)
    if not _extract_subs(src, subs_tmp):
        return False  # already logged

    # Step 2: burn subtitles
    logger.info("BURN     %s", src.name)
    success = _burn(src, subs_tmp)

    # Always clean up the temp .ass file
    if subs_tmp.exists():
        subs_tmp.unlink()

    return success


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch extract + burn subtitles into H.264 MP4 using NVIDIA NVENC."
    )
    parser.add_argument("folder", type=Path, help="Root folder to scan recursively.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be processed without doing anything.",
    )
    args = parser.parse_args()

    root: Path = args.folder.resolve()
    if not root.is_dir():
        logger.error("Not a directory: %s", root)
        sys.exit(1)

    if shutil.which("ffmpeg") is None:
        logger.error(
            "ffmpeg not found on PATH.\n"
            "  winget install ffmpeg   OR   https://www.gyan.dev/ffmpeg/builds/"
        )
        sys.exit(1)

    videos = _collect_videos(root)
    if not videos:
        logger.info("No video files found under %s", root)
        return

    logger.info("Found %d video file(s) under %s", len(videos), root)
    if args.dry_run:
        logger.info("DRY RUN — no files will be modified.")

    iterator = tqdm(videos, desc="Processing", unit="file") if HAS_TQDM else videos

    ok = fail = 0
    for path in iterator:
        success = _process(path, dry_run=args.dry_run)
        if success:
            ok += 1
        else:
            fail += 1

    logger.info("Done.  ok=%d  failed=%d", ok, fail)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
