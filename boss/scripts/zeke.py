"""Zeke boss script — AI-driven using the AIPlayer decision engine.

Unlike Zeke's old deterministic script, Zeke makes real decisions each turn
by calling decide_turn() which sends the match context to Gemini and gets
back a structured AITurnDecision.

The async prepare_turn() hook fetches the decision before plan_turn() is
called, caching it on self._pending_decision. plan_turn() then reads from
that cache and maps the AI's clip choices back to the boss clip system.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from boss.boss_script import BossScript
from core.debug import debug_event
from services.ai.llm.ai_player import AITurnDecision, choose_intro_clip, decide_turn
from services.ai.core.clip_catalog import load_catalog
from services.ai.llm.characters.zeke_rules import build_zeke_rules
from services.ai.llm.opponent_analysis import cleanup_opponent_media, prepare_opponent_media
from services.content.character_profile_service import get_profile_by_name

if TYPE_CHECKING:
    from boss.boss_state import BossState
    from services.ai.core.ai_match_state import AIMatchState

logger = logging.getLogger(__name__)

# Root of Zeke's clip library — must match where the files live.
ZEKE_CLIPS_ROOT = Path("E:/D2/Python/Anime Arena/assets/boss_clips/zeke")


# ---------------------------------------------------------------------------
# ZekeScript
# ---------------------------------------------------------------------------


class ZekeScript(BossScript):
    """AI-driven script for Zeke Yeager (Beast Titan).

    Each turn:
      1. prepare_turn() (async) calls the AI and caches the decision.
      2. plan_turn() reads the cached decision and returns clip paths.

    Zeke has no defense - he must always prioritize offensive actions.
    Submits no boss action if the AI call and model fallbacks all fail.
    """

    def __init__(self) -> None:
        self._pending_decision = None
        self._pending_intro = None
        self._intro_played = False
        self._ai_decision_failed = False
        self._ai_state = None
        self._last_analyzed_turn = None
        self._opponent_profile = None
        self._opponent_character_name = "the opponent"
        self._profile_loaded = False
        self._has_respawned = False

        # Load clip catalog and character rules once at construction time.
        try:
            self._catalog = load_catalog(ZEKE_CLIPS_ROOT)
            self._rules = build_zeke_rules(ZEKE_CLIPS_ROOT)
            logger.info("ZekeBossScript: clip catalog loaded (%d categories).", len(self._catalog.clips_by_category))
        except Exception:
            logger.exception("ZekeBossScript: failed to load clip catalog — Zeke will use fallback attacks only.")
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
        """Ask the AI to choose Zeke's opening clip."""
        if self._catalog is None or not hasattr(self._rules, "intros"):
            return

        intros = [c for c in self._catalog.clips_by_category.get("intros", [])]
        chosen = await choose_intro_clip(
            character_name="Zeke",
            series="Attack on Titan",
            intro_clips=intros,
            match_id=state.match_id,
        )
        self._pending_intro = chosen




    async def prepare_turn(self, state: BossState) -> None:
        """Build AI context and get a turn decision from the LLM."""
        if self._catalog is None or self._rules is None:
            self._pending_decision = None
            return

        # Only regenerate opponent profile when needed (first turn).
        if not self._profile_loaded:
            player_char_id = state.player1_char_id
            try:
                profile = await get_profile_by_name(player_char_id)
                if profile:
                    self._opponent_profile = profile.description
                    self._opponent_character_name = profile.char_name
                    self._profile_loaded = True
            except Exception as e:
                logger.warning("Zeke: could not load opponent profile: %s", e)

        # Preserve the AI's compound history between turns while refreshing live state.
        if self._ai_state is None:
            self._ai_state = _build_ai_state_from_boss(state, self._rules, self._catalog)
        else:
            _sync_ai_state_from_boss(self._ai_state, state)
        ai_state = self._ai_state

        # Analyze the most recent player turn to feed context into the LLM.
        opponent_media: list[dict] = []
        try:
            opponent_media = await prepare_opponent_media(
                actions=getattr(state, "last_completed_turn_actions", []),
                match_id=state.match_id,
                turn=state.current_turn,
            )
        except Exception as e:
            logger.warning("Zeke failed to analyze opponent media: %s", e)
        
        # Prepare turn inputs for AI decision.
        player_attacked_last = _player_attacked_last_turn(state)
        player_last_tier = _player_last_attack_tier(state)

        debug_event(
            "zeke_turn_decision_started",
            match_id=state.match_id,
            character="Zeke",
            turn=state.current_turn,
            opponent_action_count=len(getattr(state, "last_completed_turn_actions", [])),
            opponent_media_count=len(opponent_media),
            opponent_profile=self._opponent_profile,
            opponent_character=self._opponent_character_name,
            ai_model=os.environ.get("GOOGLE_MODEL", "gemini-3.5-flash-lite"),
        )

        try:
            decision = await decide_turn(
                match_state=ai_state,
                opponent_profile=self._opponent_profile,
                opponent_attacked_last_turn=player_attacked_last,
                opponent_last_attack_tier=player_last_tier,
                opponent_media=opponent_media,
                opponent_character_name=self._opponent_character_name,
                include_intro_choice=False,
            )
            self._ai_decision_failed = False
            self._pending_decision = decision
            self._last_analyzed_turn = state.current_turn
            debug_event("zeke_turn_decision_complete", match_id=state.match_id)
        except Exception as e:
            logger.warning("Zeke AI turn decision failed: %s", e)
            debug_event(
                "zeke_ai_decision_failed",
                match_id=state.match_id,
                error_type=type(e).__name__,
                error=str(e),
            )
            self._ai_decision_failed = True
            self._pending_decision = None
        finally:
            cleanup_opponent_media(opponent_media)

    def uses_plan_turn(self) -> bool:
        return True

    def plan_actions(self, state: BossState) -> list[dict] | None:
        """Map every valid LLM action to an ordered boss action plan."""
        decision: AITurnDecision | None = self._pending_decision
        self._pending_decision = None

        if decision is None:
            return None

        planned: list[dict] = []
        for action in decision.actions:
            if action.action_type == "defense":
                logger.info("Zeke: ignoring LLM defense action; Zeke never defends.")
                continue
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

        if planned and not any(action["action_type"] == "attack" for action in planned):
            fallback_path = self._pick_fallback_attack()
            if fallback_path is not None:
                self._catalog.mark_used(fallback_path.name)
                planned.append({
                    "action_type": "attack",
                    "tier": "Normal",
                    "path": fallback_path,
                    "dialogue": None,
                })

        return planned or None

    def plan_turn(self, state: BossState) -> tuple[Path | None, str, Path | None, Path | None]:
        """Map the cached LLM decision to the boss turn contract."""
        decision: AITurnDecision | None = self._pending_decision
        self._pending_decision = None

        if decision is None:
            return self._fallback_turn(state)

        attack_action = next(
            (action for action in decision.actions if action.action_type == "attack"),
            None,
        )
        if attack_action is None:
            return self._fallback_turn(state)

        attack_path = self._resolve_clip_path(attack_action.clip_filename)
        if attack_path is not None and self._catalog is not None:
            self._catalog.mark_used(attack_action.clip_filename)
        return None, attack_action.tier or "Normal", attack_path, None

    def _resolve_clip_path(self, filename: str) -> Path | None:
        if self._catalog is None:
            return None
        entry = self._catalog.get_clip(filename)
        if entry is None:
            logger.warning("Zeke: clip '%s' not found in catalog.", filename)
            return None
        if entry.category.lower() == "intros" and self._catalog.get_available_clip(entry.filename) is None:
            return None
        return entry.path

    def _fallback_turn(self, state: BossState) -> tuple[Path | None, str, Path | None, Path | None]:
        """Use a normal attack if the LLM cannot provide a usable attack."""
        attack_path = self._pick_fallback_attack()
        if attack_path is not None and self._catalog is not None:
            self._catalog.mark_used(attack_path.name)
        return None, "Normal", attack_path, None

    def _pick_fallback_attack(self) -> Path | None:
        if self._catalog is None:
            return None
        clip = self._catalog.pick_available("attacks.normal")
        return clip.path if clip else None

    def try_respawn(self, state: BossState) -> Path | None:
        if self._has_respawned:
            return None
        respawn_path = ZEKE_CLIPS_ROOT / "defenses" / "Full rebirth (online-video-cutter.com).mp4"
        if not respawn_path.exists():
            return None
        self._has_respawned = True
        return respawn_path

    def respawn_hp(self, state: BossState) -> int:
        return 10

    def on_victory(self, state: BossState) -> Path | None:
        victory_path = ZEKE_CLIPS_ROOT / "RP" / "I won beast form.mp4"
        return victory_path if victory_path.exists() else None

    def on_defeat(self, state: BossState) -> Path | None:
        defeat_path = ZEKE_CLIPS_ROOT / "RP" / "Falls from above and dies.mp4"
        return defeat_path if defeat_path.exists() else None


def _build_ai_state_from_boss(state: BossState, rules, catalog) -> "AIMatchState":
    """Create the lightweight match state required by the LLM decision engine."""
    from boss.boss_config import BOSS_PLAYER_ID
    from services.ai.core.ai_match_state import AIMatchState

    ai_state = AIMatchState.__new__(AIMatchState)
    ai_state.match_id = state.match_id
    ai_state.player1_id = state.player1_id
    ai_state.player2_id = BOSS_PLAYER_ID
    ai_state.player1_char_id = state.player1_char_id
    ai_state.player2_char_id = state.player2_char_id
    ai_state.player1_hp = state.player1_hp
    ai_state.player2_hp = state.boss_hp
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


def _sync_ai_state_from_boss(ai_state, state: BossState) -> None:
    """Refresh live boss fields without discarding accumulated AI history."""
    ai_state.player1_hp = state.player1_hp
    ai_state.player2_hp = state.boss_hp
    ai_state.current_turn = state.current_turn
    ai_state.current_player_id = state.current_player_id
    ai_state.pending_attack = getattr(state, "pending_attack", None)
    ai_state.pending_attacker_id = getattr(state, "pending_attacker_id", None)
    ai_state.status = getattr(state, "status", "active")


def _player_attacked_last_turn(state: BossState) -> bool:
    return bool(
        state.pending_attack
        and state.pending_attacker_id == state.player1_id
    )


def _player_last_attack_tier(state: BossState) -> str | None:
    if _player_attacked_last_turn(state):
        return state.pending_attack.get("tier")
    return None
