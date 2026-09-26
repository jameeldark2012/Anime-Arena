from __future__ import annotations

from app.cogs.battle import BattleCog
from app.cogs.boss_battle import BossBattleCog
from app.cogs.general import GeneralCog
from app.cogs.matchmaking import MatchmakingCog
from app.cogs.players import PlayersCog
from app.cogs.referee import RefereeCog
from app.cogs.reserve import ReserveCog


COMMANDS_BY_COG = {
    BattleCog: {"attack", "defend", "custom", "end_turn", "object", "surrender"},
    BossBattleCog: {"boss_fight"},
    GeneralCog: {"ping"},
    MatchmakingCog: {"challenge"},
    PlayersCog: {"players", "player"},
    RefereeCog: {"ref_view", "ref_set_hp", "ref_declare_winner", "ref_boss_wins", "ref_resume", "ref_rollback"},
    ReserveCog: {"reserve", "my_character"},
}


def _command_names(cog_cls):
    app_commands = getattr(cog_cls, "__cog_app_commands__", [])
    return {command.name for command in app_commands}


def test_all_slash_commands_are_registered():
    for cog_cls, expected in COMMANDS_BY_COG.items():
        names = _command_names(cog_cls)
        assert names >= expected, f"{cog_cls.__name__} missing commands: {sorted(expected - names)}"
