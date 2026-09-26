"""Clare boss script — AI-driven using the AIPlayer decision engine.

Unlike Zeke's deterministic script, Clare makes real decisions each turn
by calling decide_turn() which sends the match context to Gemini and gets
back a structured AITurnDecision.

The async prepare_turn() hook fetches the decision before plan_turn() is
called, caching it on self._pending_decision. plan_turn() then reads from
that cache and maps the AI's clip choices back to the boss clip system.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING

from boss.boss_script import BossScript
from core.debug import debug_event
from services.ai.ai_player import AITurnDecision, choose_intro_clip, decide_turn
from services.ai.characters.clare import build_clare_rules
from services.ai.clip_catalog import load_catalog
from services.ai.opponent_analysis import cleanup_opponent_media, prepare_opponent_media
from services.content.character_profile_service import get_profile_by_name

if TYPE_CHECKING:
    from boss.boss_state import BossState
    from services.ai.ai_match_state import AIMatchState

logger = logging.getLogger(__name__)

# Root of Clare's clip library — must match where the files live.
CLARE_CLIPS_ROOT = Path("E:/D2/Fighting/Clare")


class ClareBossScript(BossScript):
    """AI-driven script for Clare from Claymore.

    Each turn:
      1. prepare_turn() (async) calls the AI and caches the decision.
      2. plan_turn() reads the cached decision and returns clip paths.

    Submits no boss action if the AI call and model fallbacks all fail.
    """

    def __init__(self) -> None:
        self._pending_decision: AITurnDecision | None = None
        self._pending_intro: str | None = None
        self._intro_played: bool = False
        self._ai_decision_failed: bool = False
        self._ai_state = None
        self._last_analyzed_turn: int | None = None
        self._opponent_profile: str | None = None
        self._opponent_character_name: str = "the opponent"
        self._profile_loaded: bool = False

        # Load clip catalog and character rules once at construction time.
        try:
            self._catalog = load_catalog(CLARE_CLIPS_ROOT)
            self._rules = build_clare_rules(CLARE_CLIPS_ROOT)
            logger.info("ClareBossScript: clip catalog loaded (%d categories).", len(self._catalog.clips_by_category))
        except Exception:
            logger.exception("ClareBossScript: failed to load clip catalog — Clare will use fallback attacks only.")
            self._catalog = None
            self._rules = None

    # ------------------------------------------------------------------
    # Intro
    # ------------------------------------------------------------------

    def on_match_start(self, state: BossState) -> Path | None:
        intro_filename = getattr(self, "_pending_intro", None)
        self._pending_intro = None
        if not intro_filename or self._catalog is None:
            return None
        clip = self._catalog.get_clip(intro_filename)
        if clip is None or clip.category.lower() != "intros":
            return None
        self._catalog.mark_used(clip.filename)
        self._intro_played = True
        return clip.path

    async def prepare_intro(self, state: BossState) -> None:
        """Ask the LLM to choose Clare's opening clip."""
        debug_event("clare_intro_preparation_started", match_id=state.match_id)
        if self._catalog is None or self._rules is None:
            self._pending_intro = None
            return
        intro_clips = [
            clip for category, clips in self._catalog.clips_by_category.items()
            if category.lower() == "intros"
            for clip in clips
        ]
        try:
            self._pending_intro = await choose_intro_clip(
                character_name=self._rules.name,
                series=self._rules.series,
                intro_clips=intro_clips,
                match_id=state.match_id,
            )
            debug_event(
                "clare_intro_selection_ready",
                match_id=state.match_id,
                selected_clip=self._pending_intro,
            )
        except Exception:
            logger.exception("ClareBossScript: intro AI decision failed — skipping intro.")
            self._pending_intro = None

    # ------------------------------------------------------------------
    # Async prep — runs before plan_turn each turn
    # ------------------------------------------------------------------

    async def prepare_turn(self, state: BossState) -> None:
        """Call the AI to decide this turn. Caches result in self._pending_decision."""
        debug_event(
            "clare_turn_preparation_started",
            match_id=state.match_id,
            turn=state.current_turn,
            player1_hp=state.player1_hp,
            boss_hp=state.boss_hp,
            state_history=state.state_history[-6:],
        )
        if self._catalog is None or self._rules is None:
            logger.warning("ClareBossScript: no catalog — skipping AI decision.")
            self._pending_decision = None
            return

        # Load opponent profile once
        if not self._profile_loaded:
            try:
                from database.models.character import Character
                opp_char = await Character.get_or_none(character_id=state.player1_char_id)
                if opp_char:
                    self._opponent_character_name = opp_char.character_name
                    self._opponent_profile = await get_profile_by_name(opp_char.character_name)
            except Exception:
                logger.exception("ClareBossScript: failed to load opponent profile.")
            self._profile_loaded = True

        # Build a lightweight match context from BossState
        from services.ai.ai_match_state import AIMatchState
        if self._ai_state is None:
            self._ai_state = _build_ai_state_from_boss(state, self._rules, self._catalog)
        else:
            _sync_ai_state_from_boss(self._ai_state, state)

        analysis_turn = state.current_turn
        opponent_media = await prepare_opponent_media(
            actions=getattr(state, "last_completed_turn_actions", []),
            match_id=state.match_id,
            turn=analysis_turn,
        )
        opponent_dialogue = _extract_opponent_dialogue(
            getattr(state, "last_completed_turn_actions", [])
        )
        if opponent_dialogue:
            self._ai_state.record_opponent_dialogue(analysis_turn, opponent_dialogue)

        try:
            decision = await decide_turn(
                self._ai_state,
                opponent_profile=self._opponent_profile,
                opponent_attacked_last_turn=_player_attacked_last_turn(state),
                opponent_last_attack_tier=_player_last_attack_tier(state),
                opponent_media=opponent_media,
                opponent_character_name=self._opponent_character_name,
                include_intro_choice=not self._intro_played,
            )
            self._ai_decision_failed = False
            if not self._intro_played:
                self._pending_intro = decision.intro_clip_filename
            descriptions = decision.opponent_analysis
            self._ai_state.record_opponent_analysis(analysis_turn, descriptions)
            self._last_analyzed_turn = analysis_turn
            debug_event(
                "clare_opponent_context_recorded",
                match_id=state.match_id,
                turn=analysis_turn,
                descriptions=descriptions,
                compound_history=self._ai_state.turn_history_log,
            )
            self._pending_decision = decision
            debug_event(
                "clare_decision_cached",
                match_id=state.match_id,
                turn=state.current_turn,
                actions=[action.model_dump(mode="json") for action in decision.actions],
                dialogue=decision.dialogue,
            )
            logger.info(
                "ClareBossScript: AI decision for turn %d — %d actions. Reasoning: %s",
                state.current_turn,
                len(decision.actions),
                decision.reasoning[:120],
            )
        except Exception:
            logger.exception("ClareBossScript: AI decision failed — no random action will be used.")
            self._ai_decision_failed = True
            self._pending_decision = None
        finally:
            cleanup_opponent_media(opponent_media)

    # ------------------------------------------------------------------
    # Scripted plan — reads from cached decision
    # ------------------------------------------------------------------

    def uses_plan_turn(self) -> bool:
        return True

    def plan_actions(self, state: BossState) -> list[dict] | None:
        """Map every LLM action to an ordered boss action plan."""
        decision = self._pending_decision
        self._pending_decision = None
        if decision is None:
            if self._ai_decision_failed:
                return []
            return None

        planned: list[dict] = []
        for action in decision.actions:
            clip_path = self._resolve_clip_path(action.clip_filename)
            if clip_path is None:
                continue
            if self._catalog is not None:
                self._catalog.mark_used(action.clip_filename)
            planned.append({
                "action_type": action.action_type,
                "tier": action.tier or "Normal",
                "path": clip_path,
                "dialogue": decision.dialogue if not planned else None,
            })

        return planned or None

    def take_turn_intro(self, state: BossState) -> Path | None:
        """Consume the intro selected inside the first combat decision."""
        if self._intro_played:
            return None
        self._intro_played = True
        filename = self._pending_intro
        self._pending_intro = None
        if not filename or self._catalog is None:
            return None
        clip = self._catalog.get_available_clip(filename)
        if clip is None or clip.category.lower() != "intros":
            return None
        self._catalog.mark_used(clip.filename)
        return clip.path

    def plan_turn(self, state: BossState) -> tuple[Path | None, str, Path | None, Path | None]:
        """Map the AI's cached decision to (pre_clip, tier, attack_clip, post_clip)."""
        decision = self._pending_decision
        self._pending_decision = None  # consume

        if decision is None:
            return self._fallback_turn(state)

        attack_action = next((a for a in decision.actions if a.action_type == "attack"), None)
        if attack_action is None:
            # No attack this turn — still need to return something for the engine
            return self._fallback_turn(state)

        tier = attack_action.tier or "Normal"
        attack_clip_path = self._resolve_clip_path(attack_action.clip_filename)
        if attack_clip_path and self._catalog is not None:
            self._catalog.mark_used(attack_action.clip_filename)

        return None, tier, attack_clip_path, None

    # ------------------------------------------------------------------
    # Respawn / victory / defeat
    # ------------------------------------------------------------------

    def try_respawn(self, state: BossState) -> Path | None:
        # Clare does not have resurrection/regeneration ability in Claymore
        # When her HP reaches 0, she is defeated permanently
        logger.info("ClareBossScript: respawn disabled - Clare does not possess resurrection ability")
        return None

    def respawn_hp(self, state: BossState) -> int:
        # Not used since try_respawn always returns None
        return 0

    def on_victory(self, state: BossState) -> Path | None:
        rp_dir = CLARE_CLIPS_ROOT / "RP"
        candidates = ["I wont forgive you, ill kill you.mp4", "Climax RP GOAT AURA I WILL KILL U.mp4"]
        for name in candidates:
            p = rp_dir / name
            if p.exists():
                return p
        return None

    def on_defeat(self, state: BossState) -> Path | None:
        rp_dir = CLARE_CLIPS_ROOT / "RP"
        candidates = ["Pants.mp4", "Panting + crackling 2.mp4"]
        for name in candidates:
            p = rp_dir / name
            if p.exists():
                return p
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_clip_path(self, filename: str) -> Path | None:
        """Find the full path for a clip filename in the catalog."""
        if self._catalog is None:
            return None
        entry = self._catalog.get_clip(filename)
        if (
            entry is not None
            and entry.category.lower() == "intros"
            and self._catalog.get_available_clip(entry.filename) is None
        ):
            return None
        if entry:
            return entry.path
        logger.warning("ClareBossScript: clip '%s' not found in catalog.", filename)
        return None

    def _pick_rp_clip(self) -> Path | None:
        """Pick a random RP clip for pre-turn flavour."""
        rp_dir = CLARE_CLIPS_ROOT / "RP"
        if not rp_dir.exists():
            return None
        clips = [p for p in rp_dir.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}]
        return random.choice(clips) if clips else None

    def _fallback_turn(self, state: BossState) -> tuple[Path | None, str, Path | None, Path | None]:
        """Return a random Normal attack as a safe fallback."""
        attack_dir = CLARE_CLIPS_ROOT / "Normal Attack"
        clips = []
        if attack_dir.exists():
            clips = [p for p in attack_dir.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}]
        attack_path = random.choice(clips) if clips else None
        if attack_path is not None and self._catalog is not None:
            self._catalog.mark_used(attack_path.name)
        return None, "Normal", attack_path, None


# ---------------------------------------------------------------------------
# Helpers for building AI context from BossState
# ---------------------------------------------------------------------------

def _build_ai_state_from_boss(state: BossState, rules, catalog) -> "AIMatchState":
    """Create a minimal AIMatchState from the current BossState for the AI call."""
    from services.ai.ai_match_state import AIMatchState
    from boss.boss_config import BOSS_PLAYER_ID

    ai_state = AIMatchState.__new__(AIMatchState)
    # Manually mirror the constructor's runtime fields without invoking DB-backed setup.
    ai_state.match_id = state.match_id
    ai_state.player1_id = state.player1_id
    ai_state.player2_id = BOSS_PLAYER_ID
    ai_state.player1_char_id = state.player1_char_id
    ai_state.player2_char_id = state.player2_char_id
    ai_state.player1_hp = state.player1_hp
    ai_state.player2_hp = state.player2_hp
    ai_state.current_turn = state.current_turn
    ai_state.current_player_id = state.current_player_id
    ai_state.pending_attack = getattr(state, "pending_attack", None)
    ai_state.pending_attacker_id = getattr(state, "pending_attacker_id", None)
    ai_state.status = getattr(state, "status", "active")
    ai_state.character_rules = rules
    ai_state.clip_catalog = catalog
    ai_state.turn_history_log = []
    ai_state.turn_context_log = []
    ai_state.opponent_clip_descriptions = {}
    ai_state.opponent_dialogue = {}
    ai_state.is_partially_awakened = False
    ai_state.is_fully_awakened = False
    ai_state.established_abilities = {}
    ai_state.used_clips = set()
    return ai_state


def _extract_opponent_dialogue(actions: list[dict]) -> list[str]:
    """Return non-empty /talk text from the opponent's completed turn."""
    return [
        dialogue.strip()
        for action in actions
        if action.get("action_type") == "talk"
        and isinstance(dialogue := action.get("dialogue"), str)
        and dialogue.strip()
    ]


def _sync_ai_state_from_boss(ai_state, state: BossState) -> None:
    """Refresh live HP and turn fields without discarding compound AI memory."""
    ai_state.player1_hp = state.player1_hp
    ai_state.player2_hp = state.player2_hp
    ai_state.current_turn = state.current_turn
    ai_state.current_player_id = state.current_player_id
    ai_state.pending_attack = getattr(state, "pending_attack", None)
    ai_state.pending_attacker_id = getattr(state, "pending_attacker_id", None)
    ai_state.status = getattr(state, "status", "active")


def _build_turn_history(state: BossState) -> list[str]:
    """Build a plain-text turn history from the BossState snapshot history."""
    summaries = []
    for snap in state.state_history[-6:]:
        summaries.append(
            f"Turn {snap['turn']}: player1_hp={snap['player1_hp']}, player2_hp={snap['player2_hp']}"
        )
    return summaries


def _player_attacked_last_turn(state: BossState) -> bool:
    """Return True if the human player's last resolved turn included an attack."""
    if state.pending_attack and state.pending_attacker_id == state.player1_id:
        return True
    return False


def _player_last_attack_tier(state: BossState) -> str | None:
    """Return the tier of the player's pending attack if one exists."""
    if state.pending_attack and state.pending_attacker_id == state.player1_id:
        return state.pending_attack.get("tier")
    return None
