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
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Literal, TypeVar

import instructor
import litellm
from pydantic import BaseModel, Field, create_model, field_validator

from core.debug import debug_event
from services.ai.core.ai_match_state import AIMatchState
from services.ai.core.clip_catalog import ClipEntry
from services.ai.llm.base import MediaFile, Message
from services.ai.llm.ollama import OllamaClient
from services.ai.llm.opponent_analysis import (
    set_last_turn_used_ollama,
    should_unload_whisper_model,
    unload_whisper_model,
    warm_whisper_model,
)
from services.ai.llm.prompt_builder import build_prompt, build_trigger_check_prompt
from services.ai.llm.rate_limiter import estimate_text_tokens
from services.media.media_service import sample_video_frames

AI_REQUEST_TIMEOUT_SECONDS = 60
FALLBACK_MODELS = (
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
)
TRIGGER_CHECK_MODEL = os.environ.get(
    "TRIGGER_CHECK_MODEL",
    "gemini/gemini-3.1-flash-lite",
)

# FALLBACK_MODELS = (
#     "gemini-2.5-flash"
# )



# ---------------------------------------------------------------------------
# Output schema (Instructor enforces this via Pydantic)
# ---------------------------------------------------------------------------

class AIAction(BaseModel):
    action_type: Literal["attack", "defense", "custom"]
    tier: Literal["Normal", "Medium", "Absolute", "Over-Absolute"] | None = Field(
        default=None,
        validate_default=True,
    )
    clip_filename: str
    reasoning: str

    @field_validator("tier")
    @classmethod
    def tier_required_for_combat(cls, v: str | None, info) -> str | None:
        action_type = info.data.get("action_type")
        if action_type in ("attack", "defense") and v is None:
            raise ValueError(f"tier is required for action_type '{action_type}'")
        return v


def _coerce_opponent_analysis_text(item: str | dict | object) -> str:
    if isinstance(item, str):
        text = item.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text
            if isinstance(parsed, dict):
                return _coerce_opponent_analysis_text(parsed)
        return text
    if isinstance(item, dict):
        text = (
            item.get("clip_description")
            or item.get("video_description")
            or item.get("description")
            or item.get("analysis")
            or item.get("text")
            or item.get("summary")
        )
        tactical = item.get("tactical_meaning")
        if text:
            text = str(text).strip()
            if tactical and str(tactical).strip() and str(tactical).strip() not in text:
                return f"{text} {str(tactical).strip()}"
            return text
        return json.dumps(item, ensure_ascii=False)
    return str(item)


def _ensure_opponent_analysis_count(analyses: list[str], expected_count: int) -> list[str]:
    if expected_count <= 0:
        return []

    normalized = [text.strip() for text in analyses if text and text.strip()]
    normalized = normalized[:expected_count]
    missing_count = expected_count - len(normalized)
    normalized.extend(
        ["No analysis was provided for this clip. Please provide one next time."]
        * missing_count
    )
    return normalized


class AIActionDecision(BaseModel):
    reasoning: str
    actions: list[AIAction]
    dialogue: str | None = None
    opponent_analysis: list[str] = Field(default_factory=list)
    intro_clip_filename: str | None = None

    @field_validator("opponent_analysis", mode="before")
    @classmethod
    def normalize_opponent_analysis(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            return [_coerce_opponent_analysis_text(v)]

        return [_coerce_opponent_analysis_text(item) for item in v]

    @field_validator("actions")
    @classmethod
    def actions_not_empty(cls, v: list[AIAction]) -> list[AIAction]:
        if not v:
            raise ValueError("actions list cannot be empty")
        return v


class AITurnDecision(AIActionDecision):
    trigger_override: dict[str, bool] = Field(default_factory=dict)


class AIIntroDecision(BaseModel):
    clip_filename: str
    reasoning: str


_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


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
    configured_triggers = match_state.character_rules.trigger_overrides or {}
    pending_triggers = {
        name: override
        for name, override in configured_triggers.items()
        if not (name in match_state.active_trigger_overrides and override["duration"] == "permanent")
    }
    trigger_results: dict[str, bool] = {}
    trigger_used_ollama = False
    turn_trigger_overrides = set(match_state.active_trigger_overrides)
    media_content: list[dict] | None = None

    if pending_triggers:
        trigger_prompt = build_trigger_check_prompt(
            pending_triggers,
            match_state.opponent_dialogue.get(match_state.current_turn, []),
        )
        for index, item in enumerate(opponent_media or [], 1):
            action = item.get("action", {})
            transcript = item.get("transcript", "")
            trigger_prompt += (
                f"\nAttached current-turn opponent video {index}: {item.get('filename', 'video')} "
                f"(action={action.get('action_type')}, tier={action.get('tier') or 'custom'}). "
                "Inspect this video for evidence relevant to the trigger conditions."
            )
            if transcript:
                trigger_prompt += f"\nClip transcript: \"{transcript}\""
        media_content = await asyncio.to_thread(_upload_opponent_media, opponent_media or [])
        trigger_results, trigger_used_ollama = await _request_trigger_classification(
            prompt=trigger_prompt,
            media_content=media_content,
            opponent_media=opponent_media or [],
            trigger_names=list(pending_triggers),
            match_id=match_state.match_id,
            turn=match_state.current_turn,
        )
        match_state.activate_trigger_overrides(trigger_results)
        turn_trigger_overrides.update(name for name, satisfied in trigger_results.items() if satisfied)
    elif configured_triggers:
        debug_event(
            "ai_trigger_classification_skipped",
            match_id=match_state.match_id,
            turn=match_state.current_turn,
            reason="all_permanent_triggers_satisfied",
            active_trigger_overrides=sorted(match_state.active_trigger_overrides),
        )

    prompt = build_prompt(
        character_rules=match_state.character_rules,
        opponent_profile=opponent_profile,
        my_hp=match_state.ai_hp,
        opponent_hp=match_state.human_hp,
        turn_number=match_state.current_turn,
        opponent_attacked_last_turn=opponent_attacked_last_turn,
        opponent_last_attack_tier=opponent_last_attack_tier,
        opponent_last_attack_descriptions=[],
        opponent_dialogue=match_state.opponent_dialogue.get(match_state.current_turn, []),
        established_abilities=match_state.established_abilities,
        turn_history=match_state.turn_history_log,
        available_clips=match_state.clip_catalog,
        opponent_character_name=opponent_character_name,
        previous_ai_dialogues=match_state.previous_ai_dialogues(),
        active_trigger_overrides=turn_trigger_overrides,
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

    model = os.environ.get("GOOGLE_MODEL", "gemini/gemini-3.5-flash-lite")
    if not model.startswith("gemini/"):
        model = f"gemini/{model}"

    if media_content is None:
        media_content = await asyncio.to_thread(_upload_opponent_media, opponent_media or [])
    message_content: str | list[dict] = prompt
    if media_content:
        message_content = media_content + [{"type": "text", "text": prompt}]

    unload_whisper_model()
    request_started = time.monotonic()
    decision, action_used_ollama = await _request_with_fallback_chain(
        prompt=prompt,
        message_content=message_content,
        opponent_media=opponent_media or [],
        response_model=AIActionDecision,
        primary_model=model,
        match_id=match_state.match_id,
        turn=match_state.current_turn,
        max_tokens=1024,
    )

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

    # Validate and reserve clips sequentially so duplicate actions in one
    # response cannot both pass availability checks.
    validated_actions: list[AIAction] = []
    for action in decision.actions:
        clip = match_state.clip_catalog.get_available_clip(action.clip_filename)
        if clip is None:
            # Best-effort fallback: find first clip of the right category
            fallback = _find_fallback_clip(match_state, action)
            if fallback:
                action = action.model_copy(update={"clip_filename": fallback.filename})
        match_state.mark_clip_used(action.clip_filename)
        validated_actions.append(action)

    # Convert opponent_analysis to list of strings if needed
    opponent_analysis_strings = [
        _coerce_opponent_analysis_text(item) for item in decision.opponent_analysis
    ]
    expected_analysis_count = len(opponent_media or [])
    if expected_analysis_count and len(opponent_analysis_strings) < expected_analysis_count:
        debug_event(
            "ai_opponent_analysis_missing_entries",
            match_id=match_state.match_id,
            turn=match_state.current_turn,
            expected_count=expected_analysis_count,
            returned_count=len(opponent_analysis_strings),
        )
    opponent_analysis_strings = _ensure_opponent_analysis_count(
        opponent_analysis_strings,
        expected_analysis_count,
    )

    match_state.record_opponent_analysis(
        match_state.current_turn,
        opponent_analysis_strings,
    )
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
    # Gemini succeeded, don't unload Whisper for next turn
    set_last_turn_used_ollama(trigger_used_ollama or action_used_ollama)
    # Don't load Whisper here - it will be loaded after the turn completes
    # to avoid blocking the turn execution
    decision_data = decision.model_dump()
    decision_data.pop("trigger_override", None)
    return AITurnDecision(
        **decision_data,
        trigger_override=trigger_results,
    ).model_copy(update={"actions": validated_actions})


async def _request_trigger_classification(
    *,
    prompt: str,
    media_content: list[dict],
    opponent_media: list[dict],
    trigger_names: list[str],
    match_id: int,
    turn: int,
) -> tuple[dict[str, bool], bool]:
    """Require a yes/no classification for every pending trigger before action selection."""
    if any(not name.isidentifier() for name in trigger_names):
        raise ValueError("Trigger names must be valid identifiers for structured classification.")

    flags_model = create_model(
        f"TriggerFlagsTurn{turn}",
        **{name: (bool, ...) for name in trigger_names},
    )
    response_model = create_model(
        f"TriggerDecisionTurn{turn}",
        trigger_override=(flags_model, ...),
    )
    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = TRIGGER_CHECK_MODEL
    if not model.startswith("gemini/"):
        model = f"gemini/{model}"
    message_content: str | list[dict] = prompt
    if media_content:
        message_content = media_content + [{"type": "text", "text": prompt}]

    try:
        response, used_ollama = await _request_with_fallback_chain(
            prompt=prompt,
            message_content=message_content,
            opponent_media=opponent_media,
            response_model=response_model,
            primary_model=model,
            match_id=match_id,
            turn=turn,
            max_tokens=256,
        )
    except Exception as exc:
        debug_event(
            "ai_trigger_classification_failed",
            match_id=match_id,
            turn=turn,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise RuntimeError("Trigger classification failed after the shared provider fallback chain.") from exc

    result = response.trigger_override.model_dump()
    debug_event(
        "ai_triggers_classified",
        match_id=match_id,
        turn=turn,
        model="ollama" if used_ollama else model,
        trigger_results=result,
    )
    return result, used_ollama


async def _request_with_fallback_chain(
    *,
    prompt: str,
    message_content: str | list[dict],
    opponent_media: list[dict],
    response_model: type[_ResponseModel],
    primary_model: str,
    match_id: int,
    turn: int,
    max_tokens: int,
) -> tuple[_ResponseModel, bool]:
    """Run Gemini candidates, then the shared local Ollama fallback."""
    client = instructor.from_litellm(litellm.completion)
    candidates = _model_candidates(primary_model)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    last_error: Exception | None = None

    for attempt, candidate_model in enumerate(candidates):
        debug_event(
            "ai_request_dispatch",
            match_id=match_id,
            turn=turn,
            model=candidate_model,
            attempt=attempt + 1,
            response_schema=response_model.__name__,
        )
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat.completions.create,
                    model=candidate_model,
                    messages=[{"role": "user", "content": message_content}],
                    response_model=response_model,
                    api_key=api_key,
                    max_tokens=max_tokens,
                    max_retries=3,
                ),
                timeout=AI_REQUEST_TIMEOUT_SECONDS,
            )
            return response, False
        except Exception as exc:
            last_error = exc
            if not _is_retryable_model_failure(exc):
                break
            if attempt + 1 < len(candidates):
                debug_event(
                    "ai_model_fallback",
                    match_id=match_id,
                    turn=turn,
                    failed_model=candidate_model,
                    next_model=candidates[attempt + 1],
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

    response = await _decide_with_ollama_fallback(
        prompt=prompt,
        opponent_media=opponent_media,
        match_id=match_id,
        turn=turn,
        primary_error=last_error,
        response_model=response_model,
    )
    return response, True


async def _decide_with_ollama_fallback(
    *,
    prompt: str,
    opponent_media: list[dict],
    match_id: int,
    turn: int,
    primary_error: Exception | None,
    response_model: type[_ResponseModel],
) -> _ResponseModel:
    """Run the shared local Qwen fallback for any structured AI request."""
    if os.environ.get("OLLAMA_FALLBACK_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("OLLAMA_FALLBACK_ENABLED is disabled.")

    model = os.environ.get(
        "OLLAMA_FALLBACK_MODEL",
        "fredrezones55/Qwen3.5-APEX:latest",
    )

    video_items: list[tuple[dict, Path]] = []
    for item in opponent_media:
        video_path = Path(item["path"])
        if video_path.is_file():
            video_items.append((item, video_path))

    total_frame_limit = max(1, int(os.environ.get("OLLAMA_MAX_VIDEO_FRAMES", "12")))
    frame_max_dimension = max(128, int(os.environ.get("OLLAMA_FRAME_MAX_SIDE", "448")))
    frame_budgets = _allocate_ollama_frame_budgets(len(video_items), total_frame_limit)
    sampled_frame_paths: list[Path] = []
    media: list[MediaFile] = []
    for (item, video_path), frame_budget in zip(video_items, frame_budgets):
        if frame_budget <= 0:
            continue
        try:
            frames = sample_video_frames(
                video_path,
                min_frames=min(3, frame_budget),
                max_frames=frame_budget,
                fps=5.0,
                max_dimension=frame_max_dimension,
            )
        except Exception as exc:
            debug_event(
                "ai_ollama_fallback_frame_sampling_failed",
                match_id=match_id,
                turn=turn,
                filename=item.get("filename"),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            frames = []
        if not frames:
            media.append(MediaFile(path=video_path, mime_type=mimetypes.guess_type(item["filename"])[0]))
            continue
        sampled_frame_paths.extend(frames)
        for frame_path in frames:
            media.append(MediaFile(path=frame_path, mime_type="image/jpeg"))

    fallback_prompt = (
        f"The primary Gemini request failed with {type(primary_error).__name__ if primary_error else 'an unknown error'}. "
        "You are the local fallback model. Follow the request below and return valid JSON matching its requested response schema. "
        "If image frames are attached, use them as evidence for the request.\n\n"
        + prompt
    )
    text_token_estimate = estimate_text_tokens(fallback_prompt)
    image_tokens_per_frame_estimate = _estimate_qwen35_frame_tokens(frame_max_dimension)
    media_token_estimate = len(sampled_frame_paths) * image_tokens_per_frame_estimate
    debug_event(
        "ai_ollama_fallback_started",
        match_id=match_id,
        turn=turn,
        model=model,
        media_count=len(media),
        sampled_frame_count=len(sampled_frame_paths),
        text_token_estimate=text_token_estimate,
        image_tokens_per_frame_estimate=image_tokens_per_frame_estimate,
        media_token_estimate=media_token_estimate,
        total_input_token_estimate=text_token_estimate + media_token_estimate,
        media_token_estimate_basis="approximate Qwen3.5 16px patches merged 2x2, square max-dimension bound",
        text_characters=len(fallback_prompt),
        total_frame_limit=total_frame_limit,
        frame_max_dimension=frame_max_dimension,
        primary_error_type=type(primary_error).__name__ if primary_error else None,
    )
    client = OllamaClient(model=model)
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(client.generate, Message(text=fallback_prompt, media=media)),
            timeout=300,
        )
        decision = _parse_ollama_response(response.text, response_model)
    finally:
        for frame_path in sampled_frame_paths:
            frame_path.unlink(missing_ok=True)
        for frame_dir in {path.parent for path in sampled_frame_paths}:
            try:
                frame_dir.rmdir()
            except OSError:
                pass

    debug_event(
        "ai_ollama_fallback_complete",
        match_id=match_id,
        turn=turn,
        model=model,
        media_count=len(media),
        response_schema=response_model.__name__,
    )
    return decision


def _allocate_ollama_frame_budgets(video_count: int, total_frame_limit: int) -> list[int]:
    if video_count <= 0 or total_frame_limit <= 0:
        return [0] * max(video_count, 0)
    base, remainder = divmod(total_frame_limit, video_count)
    return [base + int(index < remainder) for index in range(video_count)]


def _estimate_qwen35_frame_tokens(max_dimension: int) -> int:
    patch_merge_size = 16 * 2
    patches_per_side = (max_dimension + patch_merge_size - 1) // patch_merge_size
    return patches_per_side**2 + 2


def _parse_ollama_response(text: str, response_model: type[_ResponseModel]) -> _ResponseModel:
    """Parse Ollama JSON into whichever response schema the request requires."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        return response_model.model_validate_json(cleaned)
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Ollama fallback did not return a JSON decision.")
        return response_model.model_validate(json.loads(cleaned[start:end + 1]))


def _parse_ollama_decision(text: str) -> AIActionDecision:
    """Backward-compatible action decision parser."""
    return _parse_ollama_response(text, AIActionDecision)


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
        or "notfounderror" in details
        or "404" in details
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
        "If speech is audible, use the separate clip transcript as audio evidence. Do not assume the speaker is the opponent unless the video supports that identification.",
        "- Anything else relevant and visible that improves understanding of what happened",
        f"Keep the focus on {opponent_character_name}'s action. Use they/them pronouns for {opponent_character_name} throughout; do not say 'the first character' or 'another character'.",
        "Separate visible facts from uncertain interpretation. Explain the tactical meaning: attacking, defending, evading, powering up, taunting, or reacting.",
        "Return one opponent_analysis entry per attached video, in attachment order.",
    ]
    for index, item in enumerate(opponent_media, 1):
        action = item["action"]
        transcript = item.get("transcript", "")
        header = f"{index}. {item['filename']} | action={action.get('action_type')} | tier={action.get('tier') or 'custom'}"
        # Transcript is extracted but disabled by default for performance (config.env)
        # Re-enable OPPONENT_STT_ENABLED=true when dialogue becomes critical for decisions
        if transcript:
            header += f"\n   Clip transcript: \"{transcript}\""
        lines.append(header)
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


async def warm_whisper_after_turn() -> bool:
    """Load Whisper after a turn completes, if it should be kept loaded.
    
    Call this after the turn has fully finished (after clips are posted, etc.)
    to avoid blocking the turn execution with model loading.
    
    Returns True if Whisper was loaded, False otherwise.
    """
    # Only warm if Ollama was NOT used last turn
    if not should_unload_whisper_model():
        try:
            from services.ai.llm.opponent_analysis import warm_whisper_model
            return await warm_whisper_model()
        except ImportError:
            debug_event("opponent_speech_to_text_skipped", reason="faster_whisper_not_installed")
        except Exception as exc:
            debug_event(
                "opponent_speech_to_text_model_warm_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
    return False
