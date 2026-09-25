from __future__ import annotations

from pathlib import Path

from scripts.ops.batch_video_describer import BatchAnalyzer, collect_videos, load_manifest


def test_collect_videos_recurses_and_filters(tmp_path):
    root = tmp_path / "videos"
    nested = root / "nested" / "deep"
    nested.mkdir(parents=True)

    keep_a = root / "clip_a.mp4"
    keep_b = nested / "clip_b.mkv"
    skip_txt = root / "notes.txt"

    keep_a.write_bytes(b"a")
    keep_b.write_bytes(b"b")
    skip_txt.write_bytes(b"c")

    found = collect_videos(root)

    assert {str(p.relative_to(root)).replace("\\", "/") for p in found} == {"clip_a.mp4", "nested/deep/clip_b.mkv"}


def test_batch_analyzer_skips_completed_items(tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    file_path = root / "done.mp4"
    file_path.write_bytes(b"v")

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output_file = output_dir / "done.txt"
    output_file.write_text("already analyzed")

    manifest = load_manifest(output_dir)
    analyzer = BatchAnalyzer(root, output_dir=output_dir, manifest_path=output_dir / ".video_analysis_manifest.json")

    assert analyzer.is_processed(file_path) is True
    assert analyzer.get_pending_files() == []

