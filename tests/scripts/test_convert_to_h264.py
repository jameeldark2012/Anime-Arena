from __future__ import annotations

from pathlib import Path

from scripts.media import convert_to_h264


def test_collect_videos_only_returns_supported_extensions(tmp_path):
    (tmp_path / "keep.mp4").write_bytes(b"x")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "keep.mkv").write_bytes(b"x")
    (tmp_path / "ignore.txt").write_bytes(b"x")

    found = convert_to_h264._collect_videos(tmp_path)

    assert {p.name for p in found} == {"keep.mp4", "keep.mkv"}


def test_probe_reads_codec_and_audio_flag(monkeypatch):
    class FakeFFmpeg:
        @staticmethod
        def probe(_path):
            return {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ]
            }

    monkeypatch.setattr(convert_to_h264.ffmpeg, "probe", FakeFFmpeg.probe)

    codec, has_audio = convert_to_h264._probe(Path("example.mp4"))

    assert codec == "h264"
    assert has_audio is True
