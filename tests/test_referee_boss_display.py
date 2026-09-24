from __future__ import annotations

import asyncio

from app.cogs.referee import _describe_player
from boss.boss_config import BOSSES
from boss.boss_state import BossState
from services.combat_service import generate_turn_embed


def test_describe_player_uses_boss_name_for_boss_slot():
    boss_state = BossState(
        match_id=12,
        player_id=123,
        player_char_id=111,
        config=BOSSES["zeke"],
    )

    assert _describe_player(boss_state, boss_state.player1_id) == "<@123>"
    assert _describe_player(boss_state, boss_state.player2_id) == "**Zeke** (Boss)"


def test_generate_turn_embed_uses_boss_display_name_not_internal_id(monkeypatch):
    async def run_test():
        boss_state = BossState(
            match_id=12,
            player_id=123,
            player_char_id=111,
            config=BOSSES["zeke"],
        )

        async def fake_get_or_none(**kwargs):
            return None

        monkeypatch.setattr("services.combat_service.Character.get_or_none", fake_get_or_none)

        embed = await generate_turn_embed(
            boss_state,
            {
                "acting_player_id": 123,
                "actions": [],
                "resolution": None,
                "attack_sent": None,
                "winner_id": None,
                "p1_hp": 4,
                "p2_hp": 16,
                "p1_id": 123,
                "p2_id": -1,
            },
        )

        text = embed.to_dict().get("fields", [{}])[0].get("value", "")
        assert "<@-1>" not in text
        assert "**Zeke** (Boss)" in text

    asyncio.run(run_test())
