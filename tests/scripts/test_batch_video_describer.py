from __future__ import annotations

import asyncio
import json

import scripts.ops.batch_video_describer as describer_module
from scripts.ops.batch_video_describer import (
    BatchTranscriber,
    MultiModelTranscriber,
    DEFAULT_PROMPT,
    collect_videos,
    load_manifest,
)


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


def test_batch_transcriber_skips_completed_items(tmp_path, monkeypatch):
    root = tmp_path / "videos"
    root.mkdir()
    file_path = root / "done.mp4"
    file_path.write_bytes(b"v")

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    manifest_path = output_dir / ".video_analysis_manifest.json"
    manifest_path.write_text(json.dumps({"done.mp4": {"status": "done"}}))

    monkeypatch.setattr(
        describer_module.MultiModelTranscriber,
        "_init_models",
        lambda self: setattr(self, "models", [describer_module.ModelClient("FAKE", object())]),
    )

    analyzer = BatchTranscriber(
        root,
        output_dir=output_dir,
        manifest_path=manifest_path,
        api_key="test-key",
    )

    assert analyzer._is_processed(file_path) is True
    assert analyzer._get_pending_videos() == []


def test_multi_model_transcriber_initialization(monkeypatch):
    """Test that MultiModelTranscriber initializes without errors."""
    monkeypatch.setattr(describer_module, "get_client", lambda *args, **kwargs: object())

    analyzer = MultiModelTranscriber(gemini_api_key="test-key", rpm_limit=12)

    assert analyzer.gemini_api_key == "test-key"
    assert analyzer.rpm_limit == 12
    assert len(analyzer.models) == 3
    assert all(isinstance(model, describer_module.ModelClient) for model in analyzer.models)


def test_batch_transcriber_manifest_handling(tmp_path, monkeypatch):
    """Test manifest creation and loading."""
    root = tmp_path / "videos"
    root.mkdir()

    manifest_path = root / ".video_analysis_manifest.json"

    monkeypatch.setattr(
        describer_module.MultiModelTranscriber,
        "_init_models",
        lambda self: setattr(self, "models", [describer_module.ModelClient("FAKE", object())]),
    )

    analyzer = BatchTranscriber(root, manifest_path=manifest_path, api_key="test-key")

    assert analyzer.manifest == {}

    test_video = root / "test.mp4"
    test_video.write_bytes(b"test")
    output_path = root / "test.analysis.json"

    result = {
        "video_name": "test.mp4",
        "video_path": str(test_video),
        "description": "Test description",
    }

    asyncio.run(analyzer._save_result(test_video, result["description"], "FAKE"))

    assert "test.mp4" in analyzer.manifest
    assert analyzer.manifest["test.mp4"]["status"] == "done"

    loaded = load_manifest(manifest_path)
    assert loaded["test.mp4"]["status"] == "done"


def test_short_video_skip(tmp_path, monkeypatch):
    """Test that very short videos are skipped and recorded without crashing."""
    root = tmp_path / "videos"
    root.mkdir()

    test_video = root / "short_test.mp4"
    test_video.write_bytes(b"fake video data")

    monkeypatch.setattr(
        describer_module.MultiModelTranscriber,
        "_init_models",
        lambda self: setattr(self, "models", [describer_module.ModelClient("FAKE", object())]),
    )

    analyzer = BatchTranscriber(root, api_key="test-key")
    monkeypatch.setattr(describer_module, "probe_video_duration", lambda path: 0.5)

    progress_counter = {"completed": 0, "total": 1}
    asyncio.run(analyzer._process_video(test_video, analyzer.transcriber.models[0], progress_counter))

    assert progress_counter["completed"] == 1
    assert analyzer.manifest["short_test.mp4"]["status"] == "done"
    assert analyzer.manifest["short_test.mp4"]["video_path"] == str(test_video)

    output_file = root / "short_test.analysis.json"
    assert output_file.exists()
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload["description"] == "short_test"
    assert payload["video_name"] == "short_test.mp4"

