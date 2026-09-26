from __future__ import annotations

import inspect


def test_talk_command_dependencies_import():
    from services.combat.combat_service import record_talk_action
    from app.cogs.battle import BattleCog
    from app.cogs.boss_battle import BossBattleCog

    assert callable(record_talk_action)
    assert BattleCog is not None
    assert BossBattleCog is not None


def test_record_talk_action_signature():
    from services.combat.combat_service import record_talk_action

    assert list(inspect.signature(record_talk_action).parameters) == [
        "match_state",
        "player_id",
        "dialogue_text",
    ]


def test_battle_cog_has_talk_command():
    from app.cogs.battle import BattleCog

    assert hasattr(BattleCog, "talk")


def test_boss_battle_cog_has_talk_commands():
    from app.cogs.boss_battle import BossBattleCog

    assert hasattr(BossBattleCog, "submit_talk")
    assert hasattr(BossBattleCog, "boss_talk")


def test_post_turn_result_exists():
    from app.cogs.utils import post_turn_result

    assert callable(post_turn_result)