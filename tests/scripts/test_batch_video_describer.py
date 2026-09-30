from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts.ops.batch_video_describer import (
    BatchAnalyzer, 
    collect_videos, 
    load_manifest,
    MultiModelAnalyzer,
    DEFAULT_PROMPT
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


def test_batch_analyzer_skips_completed_items(tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    file_path = root / "done.mp4"
    file_path.write_bytes(b"v")

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output_file = output_dir / "done.analysis.json"
    output_file.write_text(json.dumps({
        "video_name": "done.mp4",
        "video_path": str(file_path),
        "description": "already analyzed"
    }, indent=2))

    analyzer = BatchAnalyzer(
        root, 
        output_dir=output_dir, 
        manifest_path=output_dir / ".video_analysis_manifest.json",
        api_key="test-key",
        use_local_fallback=False
    )

    assert analyzer.is_processed(file_path) is True
    assert analyzer.get_pending_files() == []


def test_multi_model_analyzer_initialization():
    """Test that MultiModelAnalyzer initializes without errors."""
    analyzer = MultiModelAnalyzer(
        gemini_api_key="test-key",
        gemini_3_1_model="gemini-3.1-pro",
        gemini_3_5_model="gemini-3.5-flash-lite",
        qwen_model="test-qwen",
        gemini_rpm_limit=12,
        use_local_fallback=False
    )
    
    # Should have None clients when API key is fake
    assert analyzer.gemini_api_key == "test-key"
    assert analyzer.gemini_3_1_model == "gemini-3.1-pro"
    assert analyzer.gemini_3_5_model == "gemini-3.5-flash-lite"
    assert analyzer.qwen_model == "test-qwen"
    assert analyzer.gemini_3_1_limiter is not None
    assert analyzer.gemini_3_5_limiter is not None
    assert analyzer.use_local_fallback == False


def test_batch_analyzer_manifest_handling(tmp_path):
    """Test manifest creation and loading."""
    root = tmp_path / "videos"
    root.mkdir()
    
    manifest_path = root / ".video_analysis_manifest.json"
    
    # Create analyzer
    analyzer = BatchAnalyzer(
        root,
        manifest_path=manifest_path,
        api_key="test-key",
        use_local_fallback=False
    )
    
    # Manifest should be empty initially
    assert analyzer.manifest == {}
    
    # Save some data
    test_video = root / "test.mp4"
    test_video.write_bytes(b"test")
    output_path = root / "test.analysis.json"
    
    result = {
        "video_name": "test.mp4",
        "video_path": str(test_video),
        "description": "Test description"
    }
    
    analyzer._record_success(test_video, result, output_path)
    
    # Manifest should now have the entry
    assert "test.mp4" in analyzer.manifest
    assert analyzer.manifest["test.mp4"]["status"] == "done"
    
    # Load manifest should work
    loaded = load_manifest(manifest_path)
    assert loaded["test.mp4"]["status"] == "done"


async def test_short_video_skip(tmp_path):
    """Test that very short videos are skipped with filename as description."""
    root = tmp_path / "videos"
    root.mkdir()
    
    # Create a test video (just metadata, not real video)
    test_video = root / "short_test.mp4"
    test_video.write_bytes(b"fake video data")
    
    analyzer = BatchAnalyzer(
        root,
        api_key="test-key",
        use_local_fallback=False
    )
    
    # Mock the video duration to be very short
    import scripts.ops.batch_video_describer as module
    original_probe = module.probe_video_duration
    
    def mock_probe_video_duration(path):
        return 0.5  # Less than MIN_DURATION_SECONDS
    
    module.probe_video_duration = mock_probe_video_duration
    
    try:
        result = await analyzer._process_single_video(test_video)
        
        # Should skip and use filename as description
        assert result["video_name"] == "short_test.mp4"
        assert result["description"] == "short_test"
    finally:
        module.probe_video_duration = original_probe

