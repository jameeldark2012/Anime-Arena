"""Clip catalog — scans a character's clip folder and loads analysis descriptions.

Each video has a sidecar .analysis.json file produced by batch_video_describer.
This module reads all of them and builds a structured index the AI can reason about.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from services.media.media_service import SUPPORTED_VIDEO_EXTENSIONS


@dataclass
class ClipEntry:
    """A single available clip with its description."""
    path: Path
    filename: str
    category: str       # folder name, e.g. "Normal Attack", "Flash step"
    description: str    # text from the .analysis.json sidecar


@dataclass
class ClipCatalog:
    """Full index of available clips for a character, grouped by category."""
    root: Path
    clips_by_category: dict[str, list[ClipEntry]] = field(default_factory=dict)
    _used_clips: set[str] = field(default_factory=set)  # Tracks used clip filenames

    def all_clips(self) -> list[ClipEntry]:
        """Flat list of all clips across all categories."""
        return [clip for clips in self.clips_by_category.values() for clip in clips]

    def all_available_clips(self) -> list[ClipEntry]:
        """Flat list of clips that haven't been used yet."""
        return [clip for clip in self.all_clips() if clip.filename not in self._used_clips]

    def clips_for(self, category: str, exclude_used: bool = True) -> list[ClipEntry]:
        """Return clips for a specific category, empty list if not found."""
        clips = self.clips_by_category.get(category, [])
        if exclude_used:
            return [clip for clip in clips if clip.filename not in self._used_clips]
        return clips

    def get_clip(self, filename: str) -> ClipEntry | None:
        """Look up a clip by filename (case-insensitive)."""
        lower = filename.lower()
        for clip in self.all_clips():
            if clip.filename.lower() == lower:
                return clip
        return None

    def get_available_clip(self, filename: str) -> ClipEntry | None:
        """Look up a clip only if it has not been used yet."""
        clip = self.get_clip(filename)
        if clip is None or clip.filename in self._used_clips:
            return None
        return clip

    def mark_used(self, clip_filename: str) -> None:
        """Mark a clip as used; using an intro consumes the whole intro category."""
        # Look up the clip first to get its actual filename (case-sensitive)
        clip = self.get_clip(clip_filename)
        if clip:
            self._used_clips.add(clip.filename)
            if clip.category.lower() == "intros":
                self._used_clips.update(
                    intro.filename
                    for intro in self.all_clips()
                    if intro.category.lower() == "intros"
                )
        else:
            # If clip not found, still add the original filename as fallback
            self._used_clips.add(clip_filename)

    def reset_used(self) -> None:
        """Clear all used marks (for testing or match restart)."""
        self._used_clips.clear()

    def get_available_clips_by_category(self) -> dict[str, list[ClipEntry]]:
        """Return clips grouped by category, excluding used ones."""
        available = {}
        for category, clips in self.clips_by_category.items():
            available_clips = [clip for clip in clips if clip.filename not in self._used_clips]
            if available_clips:
                available[category] = available_clips
        return available


def load_catalog(root: str | Path) -> ClipCatalog:
    """Scan a character's clip folder and return a populated ClipCatalog.

    Expects structure like:
        root/
            Normal Attack/
                Normal attack 1.mp4
                Normal attack 1.analysis.json
            Flash step/
                ...

    Any subfolder containing video files is treated as a category.
    Videos without a sidecar .analysis.json are included with an empty description.
    """
    base = Path(root).expanduser().resolve()
    if not base.exists():
        raise FileNotFoundError(f"Clip root does not exist: {base}")

    catalog = ClipCatalog(root=base)

    for folder in sorted(base.iterdir()):
        if not folder.is_dir():
            continue
        # Skip hidden folders like .video_analysis_manifest
        if folder.name.startswith("."):
            continue

        category = folder.name
        entries: list[ClipEntry] = []

        for video_path in sorted(folder.iterdir()):
            if not video_path.is_file():
                continue
            if video_path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
                continue

            sidecar = video_path.with_name(f"{video_path.stem}.analysis.json")
            description = ""
            if sidecar.exists():
                try:
                    data = json.loads(sidecar.read_text(encoding="utf-8"))
                    description = data.get("description", "").strip()
                except (json.JSONDecodeError, OSError):
                    pass

            entries.append(ClipEntry(
                path=video_path,
                filename=video_path.name,
                category=category,
                description=description,
            ))

        if entries:
            catalog.clips_by_category[category] = entries

    return catalog
