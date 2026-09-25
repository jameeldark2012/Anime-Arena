from __future__ import annotations

import asyncio

from scripts.ops import seed_boss


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
