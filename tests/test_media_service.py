from __future__ import annotations

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
