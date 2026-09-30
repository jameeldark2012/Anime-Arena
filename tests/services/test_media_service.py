from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services import media_service


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return b"video-bytes"


class FakeSession:
    def __init__(self, *, should_return_bytes: bool = True):
        self.should_return_bytes = should_return_bytes

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        return FakeResponse()


def test_probe_video_codec_uses_ffprobe_output(monkeypatch):
    class DummyResult:
        stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264"}]}'

    monkeypatch.setattr(media_service.subprocess, "run", lambda *args, **kwargs: DummyResult())
    monkeypatch.setattr(media_service.os, "unlink", lambda *args, **kwargs: None)

    assert media_service.probe_video_codec(b"abc") == "h264"


def test_download_clip_returns_bytes_for_200(monkeypatch):
    monkeypatch.setattr(media_service.aiohttp, "ClientSession", lambda: FakeSession())

    out = __import__("asyncio").run(media_service.download_clip("https://example.com/test.mp4"))
    assert out == b"video-bytes"


def test_validate_h264_clip_rejects_non_h264(monkeypatch):
    async def fake_validate_clip_upload(url):
        return b"video-bytes", "vp9"

    monkeypatch.setattr(media_service, "validate_clip_upload", fake_validate_clip_upload)

    out = __import__("asyncio").run(media_service.validate_h264_clip("https://example.com/test.mp4"))
    assert out == (b"video-bytes", "vp9")


def test_sample_video_frames_uses_duration_scaled_fps(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")

    monkeypatch.setattr(media_service.shutil, "which", lambda prog: "/usr/bin/ffmpeg" if prog == "ffmpeg" else None)
    monkeypatch.setattr(media_service, "probe_video_duration", lambda _: 20.0)

    expected_count = 100
    commands = []

    def fake_run(cmd, capture_output, check, **kwargs):
        commands.append(cmd)
        frame_dir = Path(cmd[-1]).parent
        frame_dir.mkdir(parents=True, exist_ok=True)
        for i in range(expected_count):
            (frame_dir / f"frame_{i:02d}.jpg").write_bytes(b"jpg")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(media_service.subprocess, "run", fake_run)

    frames = media_service.sample_video_frames(
        video,
        min_frames=8,
        max_frames=120,
        fps=5.0,
        max_dimension=448,
    )

    assert len(frames) == expected_count
    assert all(frame.suffix.lower() == ".jpg" for frame in frames)
    assert "-vf" in commands[0]
    assert "scale=448:448:force_original_aspect_ratio=decrease" in commands[0]
