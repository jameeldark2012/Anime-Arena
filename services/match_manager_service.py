from __future__ import annotations

import asyncio
import io
import logging
import discord

from core.config import settings
from database.models.player import Player
from database.models.character import Character

logger = logging.getLogger(__name__)


class MatchState:
    def __init__(
        self,
        match_id: int,
        player1_id: int,
        player2_id: int,
        player1_char_id: int,
        player2_char_id: int,
    ) -> None:
        self.match_id = match_id
        self.player1_id = player1_id
        self.player2_id = player2_id
        self.player1_char_id = player1_char_id
        self.player2_char_id = player2_char_id
        self.player1_hp = 4
        self.player2_hp = 4
        self.current_turn = 1

        # ---------- turn ownership --------------------------------------------
        # Only one player acts at a time. Challenger (player1) goes first.
        self.current_player_id: int = player1_id

        # ---------- current-turn action list ----------------------------------
        # Ordered list of actions the active player has submitted this turn.
        # Each entry: {"action_type": "attack"|"defense"|"custom",
        #              "tier": str | None, "attachment_url": str, "filename": str}
        self.current_turn_actions: list[dict] = []

        # ---------- pending attack --------------------------------------------
        # The unresolved attack carried from the previous player's turn.
        # None means no incoming attack to respond to.
        # Set to the attack dict when the attacker ends their turn.
        # Cleared once it resolves (first action of the defender's turn).
        self.pending_attack: dict | None = None

        # ---------- pending attack owner --------------------------------------
        # Who owns the pending attack (for display purposes).
        self.pending_attacker_id: int | None = None

        # ---------- damage resolved this turn ---------------------------------
        # Populated at the start of a turn if there was a pending attack.
        # Holds the result so the embed can describe what happened.
        self.last_resolution: dict | None = None

        # In-memory video cache {player_id: [bytes, ...]}
        self.video_cache: dict[int, list[bytes]] = {}

        self.status = "active"  # active, finished
        self.has_objection = False
        self.winner_id: int | None = None
        self.loser_id: int | None = None

        # ---------- referee controls ------------------------------------------
        # When True, all combat actions are blocked until a referee resumes.
        self.is_paused: bool = False

        # Ordered list of state snapshots taken at the end of each completed turn.
        # Each snapshot is a plain dict capturing the full restorable game state.
        # Index 0 = state before turn 1 (match start), index N = after turn N.
        self.state_history: list[dict] = []
        # Immediately snapshot the initial state (turn 0).
        self._snapshot()

    # ------------------------------------------------------------------
    def _snapshot(self) -> None:
        """Capture the current restorable game state and push it to history."""
        import copy
        self.state_history.append({
            "turn": self.current_turn,
            "player1_hp": self.player1_hp,
            "player2_hp": self.player2_hp,
            "current_player_id": self.current_player_id,
            "pending_attack": copy.deepcopy(self.pending_attack),
            "pending_attacker_id": self.pending_attacker_id,
            "status": self.status,
        })

    def restore_snapshot(self, index: int) -> bool:
        """Restore game state to snapshot at `index`. Returns False if index is invalid."""
        if index < 0 or index >= len(self.state_history):
            return False
        snap = self.state_history[index]
        import copy
        self.player1_hp = snap["player1_hp"]
        self.player2_hp = snap["player2_hp"]
        self.current_player_id = snap["current_player_id"]
        self.pending_attack = copy.deepcopy(snap["pending_attack"])
        self.pending_attacker_id = snap["pending_attacker_id"]
        self.current_turn = snap["turn"]
        self.status = snap["status"]
        # Trim history forward of the restored point.
        self.state_history = self.state_history[:index + 1]
        # Clear any in-progress turn state.
        self.current_turn_actions = []
        self.last_resolution = None
        self.is_paused = False
        self.has_objection = False
        return True


class MatchManagerService:
    def __init__(self) -> None:
        self._active_matches: dict[int, MatchState] = {}
        # Tracks active match pairs as frozensets so {A, B} == {B, A}.
        self._active_pairs: set[frozenset] = set()

    async def create_match_post(
        self,
        guild: discord.Guild,
        player1_id: int,
        player2_id: int,
    ) -> tuple[MatchState | None, str | None]:
        """Creates a forum post in the configured matches channel and initializes MatchState."""
        if not settings.MATCHES_FORUM_CHANNEL_ID:
            return None, "MATCHES_FORUM_CHANNEL_ID is not configured in environment settings."

        # Reject only if this exact pair already has an active match together.
        pair = frozenset({player1_id, player2_id})
        if pair in self._active_pairs:
            return None, "These two players already have an active match against each other!"

        channel = guild.get_channel(settings.MATCHES_FORUM_CHANNEL_ID)
        if not isinstance(channel, discord.ForumChannel):
            return None, "Configured MATCHES_FORUM_CHANNEL_ID is not a valid Forum Channel."

        p1_char = await Character.get_or_none(claimed_by_id=player1_id).select_related("anime")
        p2_char = await Character.get_or_none(claimed_by_id=player2_id).select_related("anime")

        if not p1_char or not p2_char:
            return None, "Both players must have a claimed character to start a match."

        # Ensure members are fetched or fallback if needed
        m1 = guild.get_member(player1_id) or await guild.fetch_member(player1_id)
        m2 = guild.get_member(player2_id) or await guild.fetch_member(player2_id)

        p1_name = m1.display_name if m1 else str(player1_id)
        p2_name = m2.display_name if m2 else str(player2_id)

        thread_name = f"Match: {p1_name} ({p1_char.character_name}) vs {p2_name} ({p2_char.character_name})"
        
        initial_message = (
            f"⚔️ **ANIME ARENA MATCH STARTED** ⚔️\n"
            f"<@{player1_id}> (**{p1_char.character_name}**) vs <@{player2_id}> (**{p2_char.character_name}**)\n\n"
            f"**Rules Reminder:**\n"
            f"- HP starts at 4. Tiers: Normal (1), Medium (2), Absolute (3), Over-Absolute (4).\n"
            f"- Turns are **sequential** — one player acts at a time.\n"
            f"- If your opponent attacked last turn, your **first action must be a defense** (same tier or higher to block). Anything else = full damage.\n"
            f"- Use `/attack`, `/defend`, `/custom` to submit clips, then `/end_turn` to pass the turn.\n"
            f"- You can attack once per turn. Defense can be stacked. Custom clips are saved but don't affect damage.\n"
            f"- Use `/object` to flag a questionable move and ping a referee.\n\n"
            f"**Turn 1 — <@{player1_id}> goes first!**"
        )

        try:
            forum_thread = await channel.create_thread(
                name=thread_name[:100],
                content=initial_message,
            )
        except Exception:
            logger.exception("Failed to create forum post for match")
            return None, "Failed to create match post in the forum channel."

        match_state = MatchState(
            match_id=forum_thread.thread.id,
            player1_id=player1_id,
            player2_id=player2_id,
            player1_char_id=p1_char.character_id,
            player2_char_id=p2_char.character_id,
        )

        self._active_matches[match_state.match_id] = match_state
        self._active_pairs.add(pair)
        return match_state, None

    def get_match(self, match_id: int) -> MatchState | None:
        return self._active_matches.get(match_id)

    def remove_match(self, match_id: int) -> MatchState | None:
        match_state = self._active_matches.pop(match_id, None)
        if match_state:
            self._active_pairs.discard(frozenset({match_state.player1_id, match_state.player2_id}))
        return match_state


match_manager = MatchManagerService()
