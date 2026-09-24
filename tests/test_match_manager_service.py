from __future__ import annotations

import asyncio

import discord

from core.config import settings
from services import match_manager_service
from services.match_manager_service import MatchManagerService


class FakeMember:
    def __init__(self, display_name: str):
        self.display_name = display_name


class FakeThread:
    def __init__(self, thread_id: int, parent_id: int):
        self.id = thread_id
        self.parent_id = parent_id


class FakeForumChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.thread = FakeThread(thread_id=9001, parent_id=channel_id)

    async def create_thread(self, name: str, content: str):
        return type("ThreadResult", (), {"thread": self.thread})()

    def get_thread(self, thread_id: int):
        return self.thread if thread_id == self.thread.id else None


class FakeGuild:
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self.channel = FakeForumChannel(channel_id)

    def get_channel(self, channel_id: int):
        return self.channel if channel_id == self.channel_id else None

    def get_member(self, user_id: int):
        return {1: FakeMember("Alpha"), 2: FakeMember("Bravo")}.get(user_id)

    async def fetch_member(self, user_id: int):
        return {1: FakeMember("Alpha"), 2: FakeMember("Bravo")}.get(user_id)


class FakeCharacterRecord:
    def __init__(self, character_id: int, character_name: str):
        self.character_id = character_id
        self.character_name = character_name


class FakeQueryResult:
    def __init__(self, value):
        self.value = value

    def select_related(self, *_args, **_kwargs):
        return self

    def __await__(self):
        async def _resolve():
            return self.value

        return _resolve().__await__()


class FakeInteraction:
    def __init__(self, channel_id: int, channel):
        self.channel_id = channel_id
        self.channel = channel


def test_create_match_post_and_lookup(monkeypatch):
    async def run_flow():
        match_manager = MatchManagerService()
        forum_channel_id = 333
        guild = FakeGuild(forum_channel_id)

        fake_chars = {
            1: FakeCharacterRecord(character_id=101, character_name="Claire"),
            2: FakeCharacterRecord(character_id=202, character_name="Zeke"),
        }

        def fake_get_or_none(claimed_by_id):
            return FakeQueryResult(fake_chars.get(claimed_by_id))

        monkeypatch.setattr(settings, "MATCHES_FORUM_CHANNEL_ID", forum_channel_id)
        monkeypatch.setattr(match_manager_service.Character, "get_or_none", fake_get_or_none)
        monkeypatch.setattr(match_manager_service.discord, "ForumChannel", FakeForumChannel)
        monkeypatch.setattr(match_manager_service.discord, "Thread", FakeThread)

        state, error = await match_manager.create_match_post(guild, player1_id=1, player2_id=2)

        assert error is None
        assert state is not None
        assert state.match_id == guild.channel.thread.id
        assert state.player1_id == 1
        assert state.player2_id == 2
        assert state.current_player_id == 1
        assert frozenset({1, 2}) in match_manager._active_pairs
        assert match_manager.get_match(state.match_id) is state

        interaction = FakeInteraction(channel_id=state.match_id, channel=guild.channel)
        assert match_manager.get_match_for_interaction(interaction) is state

        removed = match_manager.remove_match(state.match_id)
        assert removed is state
        assert match_manager.get_match(state.match_id) is None

    asyncio.run(run_flow())
