"""AI player — decision engine for a single turn.

Uses Instructor (backed by LiteLLM + Gemini) to enforce structured output.
Takes an AIMatchState and returns a validated AITurnDecision.

Usage:
    decision = await decide_turn(
        match_state=state,
        opponent_profile="...",
        opponent_attacked_last_turn=True,
        opponent_last_attack_tier="Medium",
    )
    for action in decision.actions:
        clip = state.clip_catalog.get_clip(action.clip_filename)
        # submit via combat service functions
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
import time
from pathlib import Path
from typing import Literal

import instructor
import litellm
from pydantic import BaseModel, Field, field_validator

from core.debug import debug_event
from services.ai.ai_match_state import AIMatchState
from services.ai.clip_catalog import ClipEntry
from services.ai.prompt_builder import build_prompt
from services.ai.rate_limiter import estimate_text_tokens

AI_REQUEST_TIMEOUT_SECONDS = 60
FALLBACK_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


# ---------------------------------------------------------------------------
# Output schema (Instructor enforces this via Pydantic)
# ---------------------------------------------------------------------------

class AIAction(BaseModel):
    action_type: Literal["attack", "defense", "custom"]
    tier: Literal["Normal", "Medium", "Absolute", "Over-Absolute"] | None = None
    clip_filename: str
    reasoning: str

    @field_validator("tier")
    @classmethod
    def tier_required_for_combat(cls, v: str | None, info) -> str | None:
        action_type = info.data.get("action_type")
        if action_type in ("attack", "defense") and v is None:
            raise ValueError(f"tier is required for action_type '{action_type}'")
        return v


class AITurnDecision(BaseModel):
    reasoning: str
    actions: list[AIAction]
    dialogue: str | None = None
    opponent_analysis: list[str] = Field(default_factory=list)
    intro_clip_filename: str | None = None

    @field_validator("actions")
    @classmethod
    def actions_not_empty(cls, v: list[AIAction]) -> list[AIAction]:
        if not v:
            raise ValueError("actions list cannot be empty")
        return v


class AIIntroDecision(BaseModel):
    clip_filename: str
    reasoning: str


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------

async def decide_turn(
    match_state: AIMatchState,
    *,
    opponent_profile: str | None = None,
    opponent_attacked_last_turn: bool = False,
    opponent_last_attack_tier: str | None = None,
    opponent_media: list[dict] | None = None,
    opponent_character_name: str = "the opponent",
    include_intro_choice: bool = False,
) -> AITurnDecision:
    """Run one AI decision cycle and return a validated AITurnDecision.

    Parameters
    ----------
    match_state:
        The current AIMatchState — provides HP, clip catalog, character rules, history.
    opponent_profile:
        The opponent character's profile text from the DB.
    opponent_attacked_last_turn:
        Whether the opponent submitted an attack on their last turn.
    opponent_last_attack_tier:
        The declared tier of the opponent's last attack if they attacked.
    """
    prompt = build_prompt(
        character_rules=match_state.character_rules,
        opponent_profile=opponent_profile,
        my_hp=match_state.ai_hp,
        opponent_hp=match_state.human_hp,
        turn_number=match_state.current_turn,
        opponent_attacked_last_turn=opponent_attacked_last_turn,
        opponent_last_attack_tier=opponent_last_attack_tier,
        opponent_last_attack_descriptions=match_state.latest_opponent_descriptions(),
        opponent_dialogue=match_state.latest_opponent_dialogue(),
        established_abilities=match_state.established_abilities,
        turn_history=match_state.turn_history_log,
        available_clips=match_state.clip_catalog,
        opponent_character_name=opponent_character_name,
        previous_ai_dialogues=match_state.previous_ai_dialogues(),
    )
    if opponent_media:
        prompt += _build_opponent_media_instructions(
            opponent_media,
            opponent_character_name=opponent_character_name,
        )
    if include_intro_choice:
        prompt += _build_intro_choice_instructions(match_state)

    prompt_tokens_estimate = estimate_text_tokens(prompt)
    debug_event(
        "ai_prompt_constructed",
        match_id=match_state.match_id,
        turn=match_state.current_turn,
        prompt=prompt,
        prompt_characters=len(prompt),
    )
    debug_event(
        "ai_prompt_tokens",
        match_id=match_state.match_id,
        turn=match_state.current_turn,
        estimate=prompt_tokens_estimate,
        available_clip_count=sum(len(v) for v in match_state.clip_catalog.get_available_clips_by_category().values()),
        turn_history_count=len(match_state.turn_history_log),
    )

    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GOOGLE_MODEL", "gemini/gemini-3.5-flash-lite")
    if not model.startswith("gemini/"):
        model = f"gemini/{model}"

    # Patch LiteLLM client with Instructor for structured output enforcement
    client = instructor.from_litellm(litellm.completion)
    media_content = await asyncio.to_thread(_upload_opponent_media, opponent_media or [])
    message_content: str | list[dict] = prompt
    if media_content:
        message_content = media_content + [{"type": "text", "text": prompt}]

    decision: AITurnDecision | None = None
    request_started = time.monotonic()
    last_error: Exception | None = None
    for attempt, candidate_model in enumerate(_model_candidates(model)):
        debug_event(
            "ai_request_dispatch",
            match_id=match_state.match_id,
            turn=match_state.current_turn,
            model=candidate_model,
            attempt=attempt + 1,
            prompt_tokens_estimate=prompt_tokens_estimate,
        )
        request_started = time.monotonic()
        try:
            decision = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat.completions.create,
                    model=candidate_model,
                    messages=[{"role": "user", "content": message_content}],
                    response_model=AITurnDecision,
                    api_key=api_key,
                    max_tokens=1024,
                    max_retries=3,
                ),
                timeout=AI_REQUEST_TIMEOUT_SECONDS,
            )
            model = candidate_model
            break
        except Exception as exc:
            last_error = exc
            has_next_model = attempt + 1 < len(_model_candidates(model))
            if not has_next_model or not _is_retryable_model_failure(exc):
                debug_event(
                    "ai_request_failed",
                    match_id=match_state.match_id,
                    turn=match_state.current_turn,
                    model=candidate_model,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise
            debug_event(
                "ai_model_fallback",
                match_id=match_state.match_id,
                turn=match_state.current_turn,
                failed_model=candidate_model,
                next_model=_model_candidates(model)[attempt + 1],
                error_type=type(exc).__name__,
                error=str(exc),
            )

    if decision is None and last_error is not None:
        raise last_error

    debug_event(
        "ai_response_received",
        match_id=match_state.match_id,
        turn=match_state.current_turn,
        elapsed_seconds=round(time.monotonic() - request_started, 2),
        action_count=len(decision.actions),
        response=decision.model_dump(mode="json"),
        actions=[
            {
                "action_type": action.action_type,
                "tier": action.tier,
                "clip_filename": action.clip_filename,
            }
            for action in decision.actions
        ],
        has_dialogue=bool(decision.dialogue),
    )

    # Post-validation: verify clip filenames exist in the catalog
    validated_actions: list[AIAction] = []
    for action in decision.actions:
        clip = match_state.clip_catalog.get_available_clip(action.clip_filename)
        if clip is None:
            # Best-effort fallback: find first clip of the right category
            fallback = _find_fallback_clip(match_state, action)
            if fallback:
                action = action.model_copy(update={"clip_filename": fallback.filename})
        validated_actions.append(action)

    # Mark each clip as used so it won't be available again in this match
    for action in validated_actions:
        match_state.mark_clip_used(action.clip_filename)

    match_state.record_ai_response(
        match_state.current_turn,
        validated_actions,
        dialogue=decision.dialogue,
    )
    debug_event(
        "ai_actions_validated_and_history_recorded",
        match_id=match_state.match_id,
        turn=match_state.current_turn,
        actions=[action.model_dump(mode="json") for action in validated_actions],
        turn_history=match_state.turn_history_log,
        opponent_descriptions=match_state.latest_opponent_descriptions(),
        opponent_dialogue=match_state.latest_opponent_dialogue(),
    )
    return decision.model_copy(update={"actions": validated_actions})


def _model_candidates(primary_model: str) -> list[str]:
    normalized_primary = primary_model if primary_model.startswith("gemini/") else f"gemini/{primary_model}"
    candidates = [normalized_primary]
    for fallback in FALLBACK_MODELS:
        normalized = fallback if fallback.startswith("gemini/") else f"gemini/{fallback}"
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _is_retryable_model_failure(error: Exception) -> bool:
    details = f"{type(error).__name__}: {error}".lower()
    return (
        "503" in details
        or "serviceunavailable" in details
        or "currently experiencing high demand" in details
        or "timeout" in details
        or "invalid argument" in details
        or "badrequesterror" in details
    )


def _build_opponent_media_instructions(
    opponent_media: list[dict],
    *,
    opponent_character_name: str,
) -> str:
    lines = [
        "\n\n## Attached Opponent Videos To Analyze In This Same Request",
        f"These are combat videos of {opponent_character_name}'s submitted actions this turn. Analyze every attached video before choosing actions.",
        "For every clip, write 2-3 sentences of plain prose and cover as many of the following as are visible:",
        f"- What action {opponent_character_name} performed",
        f"- How they performed it: technique, body mechanics, speed, weapon use",
        f"- Their own position or stance: standing, crouching, mid-air, behind the opponent, etc.",
        f"- Where they performed the action relative to the opponent: front, behind, above, flanking, etc.",
        f"- Where the action landed, if applicable: head, torso, limb, etc., and what result it caused",
        f"- If they say anything, quote their words exactly; this is mandatory when audible",
        "- Anything else relevant and visible that improves understanding of what happened",
        f"Keep the focus on {opponent_character_name}'s action. Use they/them pronouns for {opponent_character_name} throughout; do not say 'the first character' or 'another character'.",
        "Separate visible facts from uncertain interpretation. Explain the tactical meaning: attacking, defending, evading, powering up, taunting, or reacting.",
        "Return one opponent_analysis entry per attached video, in attachment order.",
    ]
    for index, item in enumerate(opponent_media, 1):
        action = item["action"]
        lines.append(
            f"{index}. {item['filename']} | action={action.get('action_type')} | tier={action.get('tier') or 'custom'}"
        )
    return "\n".join(lines)


def _build_intro_choice_instructions(match_state: AIMatchState) -> str:
    intro_clips = match_state.clip_catalog.clips_for("Intros")
    if not intro_clips:
        return "\n\nNo intro clips are available for this turn. Set intro_clip_filename to null."
    lines = [
        "\n\n## Optional Clare Intro For This Boss Turn",
        "Before combat actions, optionally choose one intro clip for Clare to post.",
        "This is separate from combat actions and must not be added to the actions array.",
        "Choose exactly one filename from the Intros category, or null if no intro is appropriate.",
    ]
    lines.extend(f"- `{clip.filename}` — {clip.description}" for clip in intro_clips)
    return "\n".join(lines)


def _upload_opponent_media(opponent_media: list[dict]) -> list[dict]:
    content: list[dict] = []
    for item in opponent_media:
        path = Path(item["path"])
        mime_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
        with path.open("rb") as file_handle:
            uploaded = litellm.create_file(
                file=file_handle,
                purpose="user_data",
                custom_llm_provider="gemini",
                extra_headers={"custom-llm-provider": "gemini"},
                api_key=os.environ.get("GEMINI_API_KEY", ""),
            )
        content.append({
            "type": "file",
            "file": {"file_id": uploaded.id, "format": mime_type},
        })
    return content


async def choose_intro_clip(
    *,
    character_name: str,
    series: str,
    intro_clips: list[ClipEntry],
    match_id: int,
) -> str | None:
    """Ask the LLM to choose the best intro from the supplied clips."""
    if not intro_clips:
        return None

    clip_lines = [
        f'- filename: "{clip.filename}"\n  description: {clip.description or "No description available."}'
        for clip in intro_clips
    ]
    prompt = (
        f"You control {character_name} from {series} in an anime battle game.\n"
        "Choose the single intro video that best establishes the character before the fight.\n"
        "Respond with valid JSON only. The filename must exactly match one listed below.\n\n"
        "Available intro clips:\n" + "\n".join(clip_lines) + "\n\n"
        '{"clip_filename": "exact filename", "reasoning": "brief explanation"}'
    )
    estimate = estimate_text_tokens(prompt)
    debug_event(
        "ai_intro_prompt_constructed",
        match_id=match_id,
        prompt=prompt,
        prompt_characters=len(prompt),
    )
    debug_event(
        "ai_intro_prompt_tokens",
        match_id=match_id,
        estimate=estimate,
        intro_clip_count=len(intro_clips),
    )

    model = os.environ.get("GOOGLE_MODEL", "gemini/gemini-3.5-flash-lite")
    if not model.startswith("gemini/"):
        model = f"gemini/{model}"
    debug_event("ai_intro_request_dispatch", match_id=match_id, model=model, prompt_tokens_estimate=estimate)

    client = instructor.from_litellm(litellm.completion)
    request_started = time.monotonic()
    try:
        decision: AIIntroDecision = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_model=AIIntroDecision,
                api_key=os.environ.get("GEMINI_API_KEY", ""),
                max_tokens=256,
                max_retries=3,
            ),
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        debug_event(
            "ai_intro_request_failed",
            match_id=match_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
    chosen = next(
        (clip.filename for clip in intro_clips if clip.filename.lower() == decision.clip_filename.lower()),
        None,
    )
    debug_event(
        "ai_intro_response_received",
        match_id=match_id,
        elapsed_seconds=round(time.monotonic() - request_started, 2),
        response=decision.model_dump(mode="json"),
        selected_clip=chosen,
    )
    return chosen


def _find_fallback_clip(match_state: AIMatchState, action: AIAction):
    """Find a fallback clip when the AI's chosen filename doesn't exist."""
    rules = match_state.character_rules
    catalog = match_state.clip_catalog

    # Find categories that match the action type and tier
    for category, info in rules.category_to_action_type.items():
        if info.get("action_type") != action.action_type:
            continue
        if action.tier and info.get("tier") != action.tier:
            continue
        clips = catalog.clips_for(category)
        if clips:
            return clips[0]
    return None


