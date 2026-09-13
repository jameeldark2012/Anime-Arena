from __future__ import annotations

import logging

import discord

from boss.boss_config import BOSSES, BossConfig, BOSS_PLAYER_ID
from boss.boss_state import BossState
from core.config import settings
from database.models.character import Character

logger = logging.getLogger(__name__)


class BossManagerService:
    """Creates and tracks active boss fight instances.

    Each fight is independent — multiple players can fight the same boss
    simultaneously, each in their own thread with their own BossState.
    Fights are keyed by the Discord forum thread ID, identical to how
    MatchManagerService works so the two systems stay consistent.
    """

    def __init__(self) -> None:
        # thread_id → BossState
        self._active_fights: dict[int, BossState] = {}
        # player_id → thread_id  (one active boss fight per player at a time)
        self._player_fights: dict[int, int] = {}

    async def create_boss_fight(
        self,
        guild: discord.Guild,
        player_id: int,
        boss_slug: str,
    ) -> tuple[BossState | None, str | None]:
        """Create a forum thread for a boss fight and initialise BossState.

        Parameters
        ----------
        guild:
            The Discord guild where the fight takes place.
        player_id:
            Discord user ID of the human challenger.
        boss_slug:
            Key in BOSSES dict (e.g. ``"zeke"``).

        Returns
        -------
        (BossState, None) on success, (None, error_message) on failure.
        """
        config: BossConfig | None = BOSSES.get(boss_slug)
        if config is None:
            return None, f"Unknown boss: `{boss_slug}`. Available: {', '.join(BOSSES)}."

        # One active boss fight per player at a time.
        if player_id in self._player_fights:
            existing_thread_id = self._player_fights[player_id]
            return None, (
                f"You already have an active boss fight! "
                f"Finish it first (thread ID: {existing_thread_id})."
            )

        if not settings.MATCHES_FORUM_CHANNEL_ID:
            return None, "MATCHES_FORUM_CHANNEL_ID is not configured in environment settings."

        channel = guild.get_channel(settings.MATCHES_FORUM_CHANNEL_ID)
        if not isinstance(channel, discord.ForumChannel):
            return None, "Configured MATCHES_FORUM_CHANNEL_ID is not a valid Forum Channel."

        # Fetch the player's claimed character.
        player_char = await Character.get_or_none(claimed_by_id=player_id).select_related("anime")
        if not player_char:
            return None, "You need a claimed character to fight the boss. Use `/reserve` first."

        # Fetch the boss character for display purposes.
        boss_char = await Character.get_or_none(character_id=config.character_id)
        boss_name = boss_char.character_name if boss_char else config.display_name

        member = guild.get_member(player_id) or await guild.fetch_member(player_id)
        player_display = member.display_name if member else str(player_id)

        thread_name = f"BOSS: {player_display} ({player_char.character_name}) vs {boss_name}"

        initial_message = (
            f"⚡ **BOSS FIGHT STARTED** ⚡\n"
            f"<@{player_id}> (**{player_char.character_name}**) vs **{boss_name}** (BOSS)\n\n"
            f"**Rules:**\n"
            f"- Your HP: **4**. Boss HP: **{config.hp}**.\n"
            f"- The boss **never defends** — hit it with everything you have.\n"
            f"- The boss attacks automatically after your turn ends.\n"
            f"- Defend against incoming boss attacks using `/defend` on your turn.\n"
            f"- Use `/attack`, `/defend`, `/custom`, then `/end_turn` as normal.\n\n"
            f"**Turn 1 — <@{player_id}> goes first!**"
        )

        try:
            forum_thread = await channel.create_thread(
                name=thread_name[:100],
                content=initial_message,
            )
        except Exception:
            logger.exception("Failed to create forum thread for boss fight")
            return None, "Failed to create a match thread. Check bot permissions."

        thread_id = forum_thread.thread.id

        boss_state = BossState(
            match_id=thread_id,
            player_id=player_id,
            player_char_id=player_char.character_id,
            config=config,
        )

        self._active_fights[thread_id] = boss_state
        self._player_fights[player_id] = thread_id

        return boss_state, None

    def get_fight(self, thread_id: int) -> BossState | None:
        """Return the BossState for the given thread, or None."""
        return self._active_fights.get(thread_id)

    def remove_fight(self, thread_id: int) -> BossState | None:
        """Remove and return the BossState for the given thread."""
        boss_state = self._active_fights.pop(thread_id, None)
        if boss_state:
            self._player_fights.pop(boss_state.player1_id, None)
        return boss_state

    def get_fight_for_player(self, player_id: int) -> BossState | None:
        """Return the active BossState for a player, or None."""
        thread_id = self._player_fights.get(player_id)
        if thread_id is None:
            return None
        return self._active_fights.get(thread_id)


# Module-level singleton — imported by the cog.
boss_manager = BossManagerService()
