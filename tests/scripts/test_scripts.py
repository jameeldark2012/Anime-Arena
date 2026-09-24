from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.db import db_tables_creation
from scripts.media import convert_to_h264
from scripts.ops import seed_boss


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


def test_db_tables_creation_calls_init_database(monkeypatch):
    calls = []

    async def fake_init_database(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(db_tables_creation, "init_database", fake_init_database)

    asyncio.run(db_tables_creation.main())

    assert calls == [{"generate_schemas": True}]


def test_seed_boss_main_links_boss_character(monkeypatch):
    created_rows = []
    updated_rows = []
    closed = {"count": 0}

    class FakePlayer:
        @staticmethod
        async def get_or_create(**kwargs):
            created_rows.append(kwargs)
            return object(), True

    class FakeCharacterRecord:
        def __init__(self):
            self.character_name = "Zeke"
            self.claimed_by_id = 42

        async def save(self, update_fields=None):
            updated_rows.append(update_fields)

    class FakeCharacter:
        @staticmethod
        async def get_or_none(**kwargs):
            return FakeCharacterRecord()

    class FakeConfig:
        def __init__(self, character_id):
            self.character_id = character_id

    async def fake_init_database():
        return None

    async def fake_close_connections():
        closed["count"] += 1

    monkeypatch.setattr(seed_boss, "Player", FakePlayer)
    monkeypatch.setattr(seed_boss, "Character", FakeCharacter)
    monkeypatch.setattr(seed_boss, "BOSSES", {"zeke": FakeConfig(9)})
    monkeypatch.setattr(seed_boss, "BOSS_PLAYER_ID", -1)
    monkeypatch.setattr(seed_boss.Tortoise, "close_connections", fake_close_connections)
    monkeypatch.setattr(seed_boss, "init_database", fake_init_database)

    asyncio.run(seed_boss.main())

    assert created_rows == [{"user_id": -1}]
    assert updated_rows == [["claimed_by_id"]]
    assert closed["count"] == 1
