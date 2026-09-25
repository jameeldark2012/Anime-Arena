"""AI match state — extends MatchState with AI-specific fields.

Follows the same pattern as BossState: inherits MatchState and adds
the extra data the AI needs to make decisions.
"""
from __future__ import annotations

from services.ai.character_rules import CharacterRules
from services.ai.clip_catalog import ClipCatalog
from services.match.match_manager_service import MatchState


class AIMatchState(MatchState):
    """A MatchState variant for a match involving an AI player.

    The AI always occupies the player2 slot. The human challenger is player1
    and goes first (same as standard PvP and BossState).

    Parameters
    ----------
    match_id:
        Discord forum thread ID.
    human_player_id:
        The human player's Discord user ID.
    human_char_id:
        The human player's claimed Character's character_id.
    ai_char_id:
        The AI character's character_id (e.g. Clare's ID in the DB).
    character_rules:
        The CharacterRules instance for the AI's character.
    clip_catalog:
        The pre-loaded ClipCatalog for the AI's clip library.
    ai_player_id:
        Synthetic Discord-like user ID for the AI player.
        Defaults to -2 to avoid collision with the boss sentinel (-1).
    """

    AI_PLAYER_ID: int = -2

    def __init__(
        self,
        match_id: int,
        human_player_id: int,
        human_char_id: int,
        ai_char_id: int,
        character_rules: CharacterRules,
        clip_catalog: ClipCatalog,
        ai_player_id: int = AI_PLAYER_ID,
    ) -> None:
        super().__init__(
            match_id=match_id,
            player1_id=human_player_id,
            player2_id=ai_player_id,
            player1_char_id=human_char_id,
            player2_char_id=ai_char_id,
        )

        self.character_rules: CharacterRules = character_rules
        self.clip_catalog: ClipCatalog = clip_catalog

        # Structured turn log — each entry is a plain-text summary of a completed turn.
        # Each summary contains both what the opponent did and how the AI answered,
        # so the next decision sees a compound context stream instead of a stripped list.
        self.turn_history_log: list[str] = []

        # Structured per-turn history for compound prompt context.
        # Each entry contains the opponent's analyzed actions plus the AI's response.
        self.turn_context_log: list[dict[str, object]] = []

        # Opponent clip descriptions per turn.
        # Key: turn number when the opponent submitted their clips.
        # Value: list of description strings from the sidecar analysis JSONs.
        self.opponent_clip_descriptions: dict[int, list[str]] = {}

        # Opponent dialogue/taunts per turn.
        # Key: turn number
        # Value: list of dialogue lines from opponent's /custom actions or chat messages
        self.opponent_dialogue: dict[int, list[str]] = {}

        # Track whether the AI has entered partial or full awakening.
        # Once True, only awakened-form clips should be chosen.
        self.is_partially_awakened: bool = False
        self.is_fully_awakened: bool = False

        # Track which specific abilities have been established/used
        # e.g., "Quicksword_shown": True, "Windcutter_used": True
        self.established_abilities: dict[str, bool] = {}

        # Track which clips have been used (filenames)
        self.used_clips: set[str] = set()

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_ai_turn(self) -> bool:
        """True when it is currently the AI's turn to act."""
        return self.current_player_id == self.player2_id

    @property
    def ai_player_id(self) -> int:
        return self.player2_id

    @property
    def human_player_id(self) -> int:
        return self.player1_id

    @property
    def ai_hp(self) -> int:
        return self.player2_hp

    @property
    def human_hp(self) -> int:
        return self.player1_hp

    # ------------------------------------------------------------------
    # History helpers
    # ------------------------------------------------------------------

    def record_turn(self, summary: str) -> None:
        """Append a plain-text turn summary to the history log."""
        if summary not in self.turn_history_log:
            self.turn_history_log.append(summary)

    def _upsert_turn_context(self, turn: int) -> dict[str, object]:
        """Fetch or create the structured context for a specific turn."""
        for entry in reversed(self.turn_context_log):
            if entry.get("turn") == turn:
                return entry
        context: dict[str, object] = {
            "turn": turn,
            "opponent_descriptions": [],
            "opponent_dialogue": [],
            "ai_response": [],
        }
        self.turn_context_log.append(context)
        return context

    def record_opponent_analysis(
        self,
        turn: int,
        descriptions: list[str],
        dialogue_lines: list[str] | None = None,
    ) -> None:
        """Store the AI-analyzed description of the opponent's last turn for later prompt context."""
        self.record_opponent_clips(turn, descriptions)
        if dialogue_lines:
            self.record_opponent_dialogue(turn, dialogue_lines)

        context = self._upsert_turn_context(turn)
        context["opponent_descriptions"] = descriptions
        context["opponent_dialogue"] = dialogue_lines or []

    def record_ai_response(self, turn: int, actions: list[str | object]) -> None:
        """Record the AI's response to an opponent turn and fold it into the compound prompt memory."""
        context = self._upsert_turn_context(turn)

        normalized_actions: list[str] = []
        for action in actions:
            if isinstance(action, str):
                normalized_actions.append(action)
                continue
            if hasattr(action, "clip_filename"):
                clip_name = getattr(action, "clip_filename", "unknown clip")
                reasoning = getattr(action, "reasoning", "")
                normalized_actions.append(f"{clip_name} ({reasoning})" if reasoning else clip_name)
                continue
            normalized_actions.append(str(action))

        context["ai_response"] = normalized_actions

        opponent_descriptions = context.get("opponent_descriptions") or self.latest_opponent_descriptions()
        opponent_summary = ""
        if isinstance(opponent_descriptions, list) and opponent_descriptions:
            opponent_summary = opponent_descriptions[0]
            if len(opponent_descriptions) > 1:
                opponent_summary = "; ".join(opponent_descriptions[:2])
        else:
            opponent_summary = "an unseen opponent action"

        response_text = ", ".join(normalized_actions) if normalized_actions else "no response"
        summary = f"Turn {turn}: Opponent did {opponent_summary}. I responded with {response_text}."
        self.record_turn(summary)

    def record_opponent_clips(self, turn: int, descriptions: list[str]) -> None:
        """Store AI-generated descriptions of the opponent's clips for a given turn."""
        self.opponent_clip_descriptions[turn] = descriptions

    def record_opponent_dialogue(self, turn: int, dialogue_lines: list[str]) -> None:
        """Store opponent's dialogue/taunts for a given turn."""
        if dialogue_lines:
            self.opponent_dialogue[turn] = dialogue_lines

    def latest_opponent_descriptions(self) -> list[str]:
        """Return the opponent's clip descriptions from the most recent turn, or empty list."""
        if not self.opponent_clip_descriptions:
            return []
        latest_turn = max(self.opponent_clip_descriptions.keys())
        return self.opponent_clip_descriptions[latest_turn]

    def latest_opponent_dialogue(self) -> list[str]:
        """Return the opponent's dialogue from the most recent turn, or empty list."""
        if not self.opponent_dialogue:
            return []
        latest_turn = max(self.opponent_dialogue.keys())
        return self.opponent_dialogue[latest_turn]

    def mark_clip_used(self, clip_filename: str) -> None:
        """Mark a clip as used and update the catalog."""
        self.used_clips.add(clip_filename)
        self.clip_catalog.mark_used(clip_filename)

    def establish_ability(self, ability_name: str) -> None:
        """Mark an ability as established/available for use."""
        self.established_abilities[ability_name] = True

    def ability_is_established(self, ability_name: str) -> bool:
        """Check if an ability has been established/shown."""
        return self.established_abilities.get(ability_name, False)
