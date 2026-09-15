"""
batch_nvenc_burn.py
-------------------
Recursively processes all video files in a folder (and its subfolders):
  1. Probes the file with ffprobe to find the best English subtitle track.
  2. Extracts that subtitle track as a temp .ass file.
  3. Burns those subtitles into an .mp4 output using NVIDIA NVENC.

The original file is left completely untouched.

Selection priority for subtitle track:
  - Must be ASS/SSA format
  - Prefers tracks whose language tag or title contains "eng" / "english"
  - Skips tracks whose title contains "sign" (Signs-Songs tracks)
  - Falls back to the first ASS track found if nothing better matches

Usage:
    python -m scripts.batch_nvenc_burn <folder>

    # Dry run — show what would be processed, touch nothing:
    python -m scripts.batch_nvenc_burn <folder> --dry-run

Requirements:
    ffmpeg + ffprobe must be installed and on PATH (with NVENC support).
    Windows:  winget install ffmpeg   OR   https://www.gyan.dev/ffmpeg/builds/
    tqdm:     pip install tqdm
"""

from __future__ import annotations

import argparse
import json
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


def _find_sub_stream(src: Path) -> int | None:
    """
    Probe src with ffprobe and return the absolute stream index of the best
    English ASS subtitle track.

    Selection logic:
      1. Must be codec ass or ssa.
      2. Skip if title contains "sign" (case-insensitive).
      3. Prefer if language tag contains "eng" OR title contains "eng"/"english".
      4. Fall back to first ASS stream if nothing preferred is found.

    Returns the absolute stream index (e.g. 4), or None if no ASS track exists.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(src),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        logger.error("ffprobe failed for %s", src.name)
        return None

    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        logger.error("ffprobe returned invalid JSON for %s", src.name)
        return None

    first_ass = None
    best_match = None

    for stream in streams:
        codec = stream.get("codec_name", "").lower()
        if codec not in ("ass", "ssa"):
            continue

        index = stream.get("index")
        tags = stream.get("tags", {})
        lang = tags.get("language", "").lower()
        title = tags.get("title", "").lower()

        # Skip Signs-Songs tracks
        if "sign" in title:
            continue

        # Track the first ASS stream as fallback
        if first_ass is None:
            first_ass = index

        # Prefer English tracks
        if "eng" in lang or "eng" in title or "english" in title:
            best_match = index
            break  # take the first English full-subs track we find

    chosen = best_match if best_match is not None else first_ass

    if chosen is None:
        logger.warning("SKIP   no ASS subtitle track found in: %s", src.name)
    else:
        # Log which track was chosen for visibility
        stream_info = next((s for s in streams if s.get("index") == chosen), {})
        tags = stream_info.get("tags", {})
        title = tags.get("title", "unknown")
        lang = tags.get("language", "?")
        logger.info(
            "SUB    stream #%d  lang=%s  title=%s  ← chosen",
            chosen, lang, title,
        )

    return chosen


def _extract_subs(src: Path, stream_index: int, subs_out: Path) -> bool:
    """
    Extract the subtitle stream at absolute index stream_index into subs_out.
    Returns True on success, False on failure.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-map", f"0:{stream_index}",
        str(subs_out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")

    if result.returncode != 0:
        logger.error("FAIL (extract)  %s\nSTDERR:\n%s", src.name, result.stderr[-3000:])
        return False

    return True


def _burn(src: Path, subs_file: Path) -> bool:
    """
    Burn subtitles into src using h264_nvenc, output as .mp4 alongside the original.
    The original file is left completely untouched.
    Returns True on success, False on failure.
    """
    out_path = src.with_suffix(".mp4")
    # Run with cwd = folder so we pass only the bare filename to ass=
    # avoiding all Windows path/special-char escaping issues in filtergraphs.
    cwd = src.parent

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-map", "0:v:0",        # first video track
        "-map", "0:a:0",        # first audio track (Japanese)
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


def _process(src: Path, dry_run: bool) -> bool:
    """
    Full pipeline for one video: probe → extract subs → burn → clean up.
    Returns True on success/skip, False on failure.
    """
    if dry_run:
        stream_index = _find_sub_stream(src)
        if stream_index is not None:
            logger.info("DRY    would process: %s", src.name)
        return True

    # Step 1: find the right subtitle stream
    stream_index = _find_sub_stream(src)
    if stream_index is None:
        return False

    # Safe temp filename — no spaces/brackets so the ass= filter doesn't choke
    subs_tmp = src.parent / "subs_temp_extracted.ass"

    # Step 2: extract it
    logger.info("EXTRACT  %s", src.name)
    if not _extract_subs(src, stream_index, subs_tmp):
        return False

    # Step 3: burn it
    logger.info("BURN     %s", src.name)
    success = _burn(src, subs_tmp)

    # Always clean up the temp .ass regardless of outcome
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
        help="Show what subtitle track would be picked per file, without converting.",
    )
    args = parser.parse_args()

    root: Path = args.folder.resolve()
    if not root.is_dir():
        logger.error("Not a directory: %s", root)
        sys.exit(1)

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            logger.error(
                "%s not found on PATH.\n"
                "  winget install ffmpeg   OR   https://www.gyan.dev/ffmpeg/builds/",
                tool,
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
