from __future__ import annotations

import asyncio

from services.match.match_manager_service import MatchState
from services.combat import combat_service


class FakeAttachment:
    def __init__(self, filename: str = "clip.mp4", url: str = "https://example.com/clip.mp4"):
        self.filename = filename
        self.url = url


def test_player_can_attack_and_turn_flips(monkeypatch):
    async def fake_download(url):
        return b"video-data"

    monkeypatch.setattr(combat_service, "download_clip", fake_download)
    monkeypatch.setattr(combat_service, "probe_video_codec", lambda data: "h264")

    async def run_flow():
        state = MatchState(match_id=100, player1_id=1, player2_id=2, player1_char_id=10, player2_char_id=20)

        ok, msg, resolution = await combat_service.record_action(
            state,
            player_id=1,
            action_type="attack",
            tier="Normal",
            attachment=FakeAttachment(),
        )

        assert ok is True
        assert state.current_turn_actions[0]["action_type"] == "attack"
        assert resolution is None

        ok, msg, summary = await combat_service.end_turn(state, 1)
        assert ok is True
        assert state.current_player_id == 2
        assert state.pending_attack is not None
        assert state.pending_attacker_id == 1
        assert summary["attack_sent"]["tier"] == "Normal"

    asyncio.run(run_flow())


def test_pending_attack_defense_resolution(monkeypatch):
    async def fake_download(url):
        return b"video-data"

    monkeypatch.setattr(combat_service, "download_clip", fake_download)
    monkeypatch.setattr(combat_service, "probe_video_codec", lambda data: "h264")

    async def run_flow():
        state = MatchState(match_id=101, player1_id=1, player2_id=2, player1_char_id=10, player2_char_id=20)
        state.current_player_id = 2
        state.pending_attack = {"action_type": "attack", "tier": "Absolute"}
        state.pending_attacker_id = 1
        state.player2_hp = 4

        ok, msg, resolution = await combat_service.record_action(
            state,
            player_id=2,
            action_type="defense",
            tier="Absolute",
            attachment=FakeAttachment(),
        )

        assert ok is True
        assert resolution is not None
        assert resolution["outcome"] == "blocked"
        assert state.player2_hp == 4
        assert state.pending_attack is None

    asyncio.run(run_flow())


def test_match_state_turn_snapshot_and_restore():
    state = MatchState(match_id=42, player1_id=1, player2_id=2, player1_char_id=10, player2_char_id=20)
    state.player1_hp = 2
    state.current_player_id = 2
    state._snapshot()

    state.player1_hp = 0
    state.current_player_id = 1
    state.restore_snapshot(1)

    assert state.player1_hp == 2
    assert state.current_player_id == 2
    assert state.state_history[0]["player1_hp"] == 4
    assert state.state_history[1]["player1_hp"] == 2
