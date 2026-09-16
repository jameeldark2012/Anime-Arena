from __future__ import annotations

from boss.boss_config import BossConfig, BOSS_PLAYER_ID
from services.match_manager_service import MatchState


class BossState(MatchState):
    """A MatchState variant for a boss fight.

    The boss always occupies the player2 slot. The human challenger is player1
    and goes first (same as standard PvP). All combat logic in combat_service.py
    works unchanged — BossState just sets up the right initial values and
    carries boss-specific metadata.

    Parameters
    ----------
    match_id:
        Discord forum thread ID (same as standard MatchState).
    player_id:
        The human player's Discord user ID.
    player_char_id:
        The human player's claimed Character's character_id.
    config:
        The BossConfig that defines this boss (hp, never_defends, etc.).
    """

    def __init__(
        self,
        match_id: int,
        player_id: int,
        player_char_id: int,
        config: BossConfig,
    ) -> None:
        super().__init__(
            match_id=match_id,
            player1_id=player_id,
            player2_id=BOSS_PLAYER_ID,
            player1_char_id=player_char_id,
            player2_char_id=config.character_id,
        )

        # Override the boss's HP with whatever the config declares.
        self.player2_hp = config.hp

        # Carry the full config for use by boss_ai.
        self.boss_config: BossConfig = config

        # Convenience flag — read by boss_ai to decide whether to defend.
        # Stored here so each BossState instance is self-contained.
        self.boss_never_defends: bool = config.never_defends

        # The script instance lives here so stateful data (used clips, respawn
        # flag, etc.) persists across every turn of the same fight.
        self.script = config.get_script()

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def boss_hp(self) -> int:
        """Current HP of the boss (player2)."""
        return self.player2_hp

    @boss_hp.setter
    def boss_hp(self, value: int) -> None:
        self.player2_hp = value

    @property
    def player_hp(self) -> int:
        """Current HP of the human player (player1)."""
        return self.player1_hp

    @property
    def is_boss_turn(self) -> bool:
        """True when it is currently the boss's turn to act."""
        return self.current_player_id == BOSS_PLAYER_ID
