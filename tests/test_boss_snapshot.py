from __future__ import annotations

from boss.boss_config import BOSSES
from boss.boss_state import BossState


def test_boss_initial_snapshot_uses_config_hp():
    config = BOSSES["zeke"]
    state = BossState(match_id=99, player_id=123, player_char_id=111, config=config)

    assert state.player2_hp == config.hp
    assert state.state_history[0]["player2_hp"] == config.hp
    assert state.state_history[0]["player1_hp"] == 4
