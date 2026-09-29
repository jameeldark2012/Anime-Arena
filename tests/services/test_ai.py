from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from boss.boss_manager import BossManagerService
from boss.scripts.clare import ClareBossScript, _build_ai_state_from_boss, _extract_opponent_dialogue
from boss.scripts import zeke as zeke_module
from boss.scripts.zeke import ZekeScript
from services.ai import RateLimiter
from services.ai.core.ai_match_state import AIMatchState
from services.ai.llm import ai_player
from services.ai.llm import gemini as gemini_module
from services.ai.llm.ai_player import AIAction, AIActionDecision, AITurnDecision
from services.ai.llm.base import MediaFile, Message
from services.ai.llm.gemini import GeminiClient
from services.ai.llm import opponent_analysis
from services.ai.llm.ollama import OllamaClient
from services.ai.llm.opponent_analysis import prepare_opponent_media
from services.ai.llm.prompt_builder import build_prompt, build_trigger_check_prompt
from services.ai.core.character_rules import CharacterRules
from services.ai.core.clip_catalog import ClipCatalog, ClipEntry, load_catalog
from services.match.match_manager_service import MatchManagerService


def test_rate_limiter_applies_safety_margin_to_tpm_and_rpm():
    rate_limiter = RateLimiter(tpm_limit=65000, rpm_limit=15, safety_margin=0.8)

    assert rate_limiter.effective_rpm == 12
    assert rate_limiter.effective_tpm == 52000


def test_combat_action_requires_tier_when_tier_is_omitted():
    with pytest.raises(ValueError, match="tier is required for action_type 'attack'"):
        AIAction.model_validate({
            "action_type": "attack",
            "clip_filename": "slash.mp4",
            "reasoning": "Attack.",
        })


def test_clip_catalog_excludes_used_clips_from_available_lookup():
    catalog = ClipCatalog(root=Path("E:/tmp"))
    catalog.clips_by_category["Normal Attack"] = [
        ClipEntry(Path("E:/tmp/first.mp4"), "first.mp4", "Normal Attack", "first"),
        ClipEntry(Path("E:/tmp/second.mp4"), "second.mp4", "Normal Attack", "second"),
    ]

    catalog.mark_used("first.mp4")

    assert catalog.get_available_clip("first.mp4") is None
    assert catalog.get_available_clip("second.mp4") is not None
    assert [clip.filename for clip in catalog.clips_for("Normal Attack")] == ["second.mp4"]


def test_marking_intro_used_excludes_every_intro_from_catalog():
    catalog = ClipCatalog(root=Path("E:/tmp"))
    catalog.clips_by_category["Intros"] = [
        ClipEntry(Path("E:/tmp/first-intro.mp4"), "first-intro.mp4", "Intros", "first"),
        ClipEntry(Path("E:/tmp/second-intro.mp4"), "second-intro.mp4", "Intros", "second"),
    ]
    catalog.clips_by_category["RP"] = [
        ClipEntry(Path("E:/tmp/rp.mp4"), "rp.mp4", "RP", "roleplay"),
    ]

    catalog.mark_used("first-intro.mp4")

    assert catalog.clips_for("Intros") == []
    assert catalog.get_available_clip("second-intro.mp4") is None
    assert catalog.get_available_clip("rp.mp4") is not None


def test_decide_turn_replaces_duplicate_actions_with_next_available_clip(monkeypatch):
    rules = CharacterRules(
        name="Test",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Focused and direct.",
        category_to_action_type={
            "Normal Attack": {"action_type": "attack", "tier": "Normal"},
        },
    )
    catalog = ClipCatalog(root=Path("E:/tmp"))
    catalog.clips_by_category["Normal Attack"] = [
        ClipEntry(Path("E:/tmp/first.mp4"), "first.mp4", "Normal Attack", "first"),
        ClipEntry(Path("E:/tmp/second.mp4"), "second.mp4", "Normal Attack", "second"),
    ]
    state = AIMatchState(
        match_id=1,
        human_player_id=2,
        human_char_id=3,
        ai_char_id=4,
        character_rules=rules,
        clip_catalog=catalog,
    )
    state.current_turn = 10
    state.record_opponent_analysis(8, ["Prior Aizen defense, unique historical marker."])
    state.record_ai_response(8, [])
    decision = AITurnDecision(
        reasoning="duplicate",
        actions=[
            AIAction(
                action_type="attack",
                tier="Normal",
                clip_filename="first.mp4",
                reasoning="first",
            ),
            AIAction(
                action_type="attack",
                tier="Normal",
                clip_filename="first.mp4",
                reasoning="duplicate",
            ),
        ],
    )

    class FakeCompletions:
        def create(self, **kwargs):
            lifecycle.append("llm")
            captured_prompt.append(kwargs["messages"][0]["content"])
            return decision

    lifecycle: list[str] = []
    captured_prompt: list[str] = []
    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(ai_player.instructor, "from_litellm", lambda _: client)
    monkeypatch.setattr(ai_player, "_model_candidates", lambda _: ["gemini/test"])
    monkeypatch.setattr(ai_player, "unload_whisper_model", lambda: lifecycle.append("unload"))
    monkeypatch.setattr(ai_player, "warm_whisper_after_turn", lambda: lifecycle.append("reload"))

    result = asyncio.run(ai_player.decide_turn(state))

    assert [action.clip_filename for action in result.actions] == ["first.mp4", "second.mp4"]
    # warm_whisper_after_turn is called after the turn completes, not during decide_turn
    assert lifecycle == ["unload", "llm"]
    current_prompt, historical_memory = captured_prompt[0].split("## Full Match Memory", 1)
    assert "Prior Aizen defense, unique historical marker." not in current_prompt
    assert "Prior Aizen defense, unique historical marker." in historical_memory
    assert "No opponent analysis was provided; please provide one next time" in state.turn_history_log[-1]


def test_permanent_trigger_override_latches_and_temporary_override_does_not():
    rules = CharacterRules(
        name="Test",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Focused and direct.",
        trigger_overrides={
            "permanent_trigger": {
                "condition": "The opponent mentions the permanent trigger.",
                "duration": "permanent",
                "replacements": {
                    "escalation": "PERMANENT ESCALATION REPLACEMENT",
                    "delayed_escalation": "PERMANENT DELAYED ESCALATION REPLACEMENT",
                    "early_turn_restraint": "PERMANENT EARLY-TURN REPLACEMENT",
                },
            },
            "temporary_trigger": {
                "condition": "The opponent mentions the temporary trigger.",
                "duration": "temporary",
                "replacements": {"escalation": "TEMPORARY RULE REPLACEMENT"},
            },
        },
    )
    state = AIMatchState(
        match_id=1,
        human_player_id=2,
        human_char_id=3,
        ai_char_id=4,
        character_rules=rules,
        clip_catalog=ClipCatalog(root=Path("E:/tmp")),
    )

    state.activate_trigger_overrides({"permanent_trigger": True, "temporary_trigger": True})

    prompt = build_prompt(
        character_rules=rules,
        opponent_profile=None,
        my_hp=4,
        opponent_hp=4,
        turn_number=2,
        opponent_attacked_last_turn=False,
        opponent_last_attack_tier=None,
        opponent_last_attack_descriptions=[],
        opponent_dialogue=[],
        established_abilities={},
        turn_history=[],
        available_clips=state.clip_catalog,
        active_trigger_overrides=state.active_trigger_overrides,
    )

    assert state.active_trigger_overrides == {"permanent_trigger"}
    assert "PERMANENT ESCALATION REPLACEMENT" in prompt
    assert "PERMANENT DELAYED ESCALATION REPLACEMENT" in prompt
    assert "PERMANENT EARLY-TURN REPLACEMENT" in prompt
    assert "On early turns, prefer restrained Normal-tier attacks" not in prompt
    assert "trigger_override" not in prompt

    trigger_prompt = build_trigger_check_prompt(
        {"temporary_trigger": rules.trigger_overrides["temporary_trigger"]},
        ["The temporary trigger was mentioned."],
    )
    assert '"<trigger_name>": true' in trigger_prompt
    assert "The opponent mentions the temporary trigger." in trigger_prompt
    assert "TEMPORARY RULE REPLACEMENT" not in trigger_prompt


def test_decide_turn_classifies_trigger_before_building_action_prompt(monkeypatch):
    rules = CharacterRules(
        name="Test",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Focused and direct.",
        category_to_action_type={"Normal Attack": {"action_type": "attack", "tier": "Normal"}},
        trigger_overrides={
            "teresa_mentioned": {
                "condition": "The opponent mentions Teresa.",
                "duration": "permanent",
                "replacements": {
                    "escalation": "TERESA ESCALATION REPLACEMENT",
                    "delayed_escalation": "TERESA DELAYED ESCALATION REPLACEMENT",
                },
            },
            "second_permanent_trigger": {
                "condition": "A second permanent condition is satisfied.",
                "duration": "permanent",
                "replacements": {"early_turn_restraint": "SECOND PERMANENT REPLACEMENT"},
            },
        },
    )
    catalog = ClipCatalog(root=Path("E:/tmp"))
    catalog.clips_by_category["Normal Attack"] = [
        ClipEntry(Path("E:/tmp/slash.mp4"), "slash.mp4", "Normal Attack", "slash")
    ]
    state = AIMatchState(1, 2, 3, 4, rules, catalog)
    state.current_turn = 1
    state.opponent_dialogue[1] = ["Teresa was stronger than you."]
    prompts: list[str] = []
    trigger_check_count = 0

    class FakeCompletions:
        def create(self, **kwargs):
            nonlocal trigger_check_count
            prompts.append(kwargs["messages"][0]["content"])
            response_model = kwargs["response_model"]
            if response_model.__name__.startswith("TriggerDecisionTurn"):
                trigger_check_count += 1
                trigger_names = response_model.model_fields["trigger_override"].annotation.model_fields
                trigger_results = {
                    name: name == "teresa_mentioned" or trigger_check_count > 1
                    for name in trigger_names
                }
                return response_model.model_validate({"trigger_override": trigger_results})
            return AIActionDecision(
                reasoning="The trigger replacement rules are active.",
                actions=[AIAction(
                    action_type="attack",
                    tier="Normal",
                    clip_filename="slash.mp4",
                    reasoning="A legal attack follows the active rules.",
                )],
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(ai_player.instructor, "from_litellm", lambda _: client)
    monkeypatch.setenv("GOOGLE_MODEL", "gemini/gemini-3.5-flash-lite")
    monkeypatch.delenv("TRIGGER_CHECK_MODEL", raising=False)
    requested_models: list[str] = []

    def model_candidates(primary_model):
        requested_models.append(primary_model)
        return [primary_model]

    monkeypatch.setattr(ai_player, "_model_candidates", model_candidates)
    monkeypatch.setattr(ai_player, "_upload_opponent_media", lambda _: [])
    monkeypatch.setattr(ai_player, "unload_whisper_model", lambda: None)

    result = asyncio.run(ai_player.decide_turn(state))

    assert len(prompts) == 2
    assert "Required trigger names: teresa_mentioned" in prompts[0]
    assert "only trigger classification" in prompts[0]
    assert "TERESA ESCALATION REPLACEMENT" not in prompts[0]
    assert "TERESA ESCALATION REPLACEMENT" in prompts[1]
    assert "TERESA DELAYED ESCALATION REPLACEMENT" in prompts[1]
    assert "trigger_override" not in prompts[1]
    assert result.trigger_override == {
        "teresa_mentioned": True,
        "second_permanent_trigger": False,
    }
    assert state.active_trigger_overrides == {"teresa_mentioned"}
    assert requested_models == [
        "gemini/gemini-3.1-flash-lite",
        "gemini/gemini-3.5-flash-lite",
    ]

    state.current_turn = 2
    second_result = asyncio.run(ai_player.decide_turn(state))

    assert second_result.trigger_override == {"second_permanent_trigger": True}
    assert state.active_trigger_overrides == {"teresa_mentioned", "second_permanent_trigger"}
    assert "Required trigger names: second_permanent_trigger" in prompts[2]
    assert requested_models[2:4] == [
        "gemini/gemini-3.1-flash-lite",
        "gemini/gemini-3.5-flash-lite",
    ]

    state.current_turn = 3
    asyncio.run(ai_player.decide_turn(state))

    assert len(prompts) == 5
    assert "Required trigger names" not in prompts[4]
    assert requested_models[-1] == "gemini/gemini-3.5-flash-lite"


def test_trigger_classifier_uses_shared_local_fallback_after_gemini_candidates(monkeypatch):
    requested_models: list[str] = []
    local_fallback_calls: list[dict] = []
    opponent_media = [{
        "filename": "opponent.mp4",
        "path": Path("E:/tmp/opponent.mp4"),
        "action": {"action_type": "talk", "tier": None},
    }]

    class FakeCompletions:
        def create(self, **kwargs):
            requested_models.append(kwargs["model"])
            raise RuntimeError("503 Service Unavailable")

    async def fake_local_fallback(**kwargs):
        local_fallback_calls.append(kwargs)
        return kwargs["response_model"].model_validate({
            "trigger_override": {"teresa_mentioned": True},
        })

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(ai_player.instructor, "from_litellm", lambda _: client)
    monkeypatch.setattr(
        ai_player,
        "_model_candidates",
        lambda _: ["gemini/gemini-3.1-flash-lite", "gemini/gemini-2.5-flash-lite"],
    )
    monkeypatch.setattr(ai_player, "_is_retryable_model_failure", lambda _: True)
    monkeypatch.setattr(ai_player, "_decide_with_ollama_fallback", fake_local_fallback)

    results, used_ollama = asyncio.run(ai_player._request_trigger_classification(
        prompt="Classify the trigger.",
        media_content=[],
        opponent_media=opponent_media,
        trigger_names=["teresa_mentioned"],
        match_id=1,
        turn=1,
    ))

    assert requested_models == [
        "gemini/gemini-3.1-flash-lite",
        "gemini/gemini-2.5-flash-lite",
    ]
    assert results == {"teresa_mentioned": True}
    assert used_ollama is True
    assert len(local_fallback_calls) == 1
    assert local_fallback_calls[0]["opponent_media"] == opponent_media
    assert local_fallback_calls[0]["response_model"].__name__.startswith("TriggerDecisionTurn")


def test_zeke_fallback_attack_excludes_catalog_used_clips(tmp_path, monkeypatch):
    catalog = ClipCatalog(root=tmp_path)
    used_path = tmp_path / "used.mp4"
    fresh_path = tmp_path / "fresh.mp4"
    catalog.clips_by_category["attacks.normal"] = [
        ClipEntry(used_path, used_path.name, "attacks.normal", "used"),
        ClipEntry(fresh_path, fresh_path.name, "attacks.normal", "fresh"),
    ]
    catalog.mark_used(used_path.name)

    script = ZekeScript.__new__(ZekeScript)
    script._catalog = catalog
    monkeypatch.setattr(zeke_module, "ZEKE_CLIPS_ROOT", tmp_path)

    assert script._pick_fallback_attack() == fresh_path


def test_load_catalog_indexes_nested_attack_categories(tmp_path):
    attack_dir = tmp_path / "attacks" / "normal"
    attack_dir.mkdir(parents=True)
    (attack_dir / "normal.mp4").touch()

    catalog = load_catalog(tmp_path)

    assert [clip.filename for clip in catalog.clips_for("attacks.normal")] == ["normal.mp4"]


def test_clare_match_start_intro_closes_intro_choice_for_rest_of_match():
    catalog = ClipCatalog(root=Path("E:/tmp"))
    catalog.clips_by_category["Intros"] = [
        ClipEntry(Path("E:/tmp/first-intro.mp4"), "first-intro.mp4", "Intros", "first"),
        ClipEntry(Path("E:/tmp/second-intro.mp4"), "second-intro.mp4", "Intros", "second"),
    ]
    script = ClareBossScript.__new__(ClareBossScript)
    script._catalog = catalog
    script._pending_intro = "first-intro.mp4"
    script._intro_played = False

    assert script.on_match_start(None) == Path("E:/tmp/first-intro.mp4")
    assert script._intro_played is True
    assert catalog.clips_for("Intros") == []
    assert script._resolve_clip_path("second-intro.mp4") is None


def test_clare_victory_uses_requested_rp_clip(tmp_path, monkeypatch):
    rp_dir = tmp_path / "RP"
    rp_dir.mkdir()
    victory_clip = rp_dir / "Sword dripping blood standing behind killed one.mp4"
    victory_clip.touch()
    monkeypatch.setattr("boss.scripts.clare.CLARE_CLIPS_ROOT", tmp_path)
    script = ClareBossScript.__new__(ClareBossScript)

    assert script.on_victory(None) == victory_clip


def test_clare_turn_intro_blocks_other_intro_actions_in_same_turn():
    catalog = ClipCatalog(root=Path("E:/tmp"))
    catalog.clips_by_category["Intros"] = [
        ClipEntry(Path("E:/tmp/first-intro.mp4"), "first-intro.mp4", "Intros", "first"),
        ClipEntry(Path("E:/tmp/second-intro.mp4"), "second-intro.mp4", "Intros", "second"),
    ]
    script = ClareBossScript.__new__(ClareBossScript)
    script._catalog = catalog
    script._pending_intro = "first-intro.mp4"
    script._intro_played = False

    assert script.take_turn_intro(None) == Path("E:/tmp/first-intro.mp4")
    assert catalog.clips_for("Intros") == []
    assert script._resolve_clip_path("second-intro.mp4") is None


def test_ai_match_state_tracks_opponent_analysis_and_ai_response_history():
    rules = CharacterRules(
        name="Test Hero",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Focused and direct.",
    )
    state = AIMatchState(
        match_id=1,
        human_player_id=10,
        human_char_id=1,
        ai_char_id=2,
        character_rules=rules,
        clip_catalog=ClipCatalog(root=Path("E:/tmp")),
    )

    state.record_opponent_dialogue(1, ["You are too slow."])
    state.record_opponent_analysis(
        turn=1,
        descriptions=["The opponent lunges forward with a rising slash from the left flank."],
    )
    state.record_ai_response(
        turn=1,
        actions=["Defense: Step back and guard low.", "Counter: Slash once from the right."],
    )

    assert any("Opponent past turn:" in entry for entry in state.turn_history_log)
    assert any("My past turn:" in entry for entry in state.turn_history_log)
    assert state.latest_opponent_descriptions() == [
        "The opponent lunges forward with a rising slash from the left flank."
    ]
    assert state.latest_opponent_dialogue() == ["You are too slow."]
    assert state.turn_context_log[0]["opponent_dialogue"] == ["You are too slow."]
    assert 'Opponent past turn: The opponent lunges forward with a rising slash from the left flank.' in state.turn_history_log[0]
    assert 'My past turn:' in state.turn_history_log[0]


def test_ai_match_state_does_not_reuse_stale_opponent_summary_for_new_turn():
    rules = CharacterRules(
        name="Test Hero",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Focused and direct.",
    )
    state = AIMatchState(
        match_id=1,
        human_player_id=10,
        human_char_id=1,
        ai_char_id=2,
        character_rules=rules,
        clip_catalog=ClipCatalog(root=Path("E:/tmp")),
    )

    state.record_opponent_analysis(1, ["Aizen stops a sword swing with his finger."])
    state.record_ai_response(1, ["Defense: Step back."])
    state.record_ai_response(2, ["Normal attack: front slash."], dialogue="I press forward.")

    assert "Aizen stops a sword swing with his finger" not in state.turn_history_log[-1]
    assert "No opponent analysis was provided; please provide one next time" in state.turn_history_log[-1]


def test_prompt_keeps_current_turn_evidence_separate_from_recent_memory():
    rules = CharacterRules(
        name="Clare",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Focused and direct.",
    )
    catalog = ClipCatalog(root=Path("E:/tmp"))
    catalog.clips_by_category["Normal Attack"] = [
        ClipEntry(Path("E:/tmp/normal.mp4"), "normal.mp4", "Normal Attack", "normal"),
    ]

    prompt = build_prompt(
        character_rules=rules,
        opponent_profile="A confident swordsman.",
        my_hp=4,
        opponent_hp=3,
        turn_number=5,
        opponent_attacked_last_turn=True,
        opponent_last_attack_tier="Normal",
        opponent_last_attack_descriptions=["The opponent steps in and cuts low."],
        opponent_dialogue=["You are too slow."],
        established_abilities={"basic_slash": True},
        turn_history=[
            "Turn 3: Historical memory - opponent analysis: the opponent lunges with a straight slash. My action summary: Ward step and counter slash.",
            "Turn 4: Historical memory - opponent analysis: the opponent feints left. My action summary: thrust and break distance.",
        ],
        available_clips=catalog,
        opponent_character_name="Aizen",
    )

    assert "## Current Turn Evidence" in prompt
    assert "## Full Match Memory" in prompt
    assert "not current-turn evidence" in prompt
    assert "Historical memory - opponent analysis" in prompt
    assert "Analyze only videos attached to this request" in prompt
    assert "If no opponent videos are attached, return an empty `opponent_analysis` list." in prompt


def test_gemini_client_reuses_uploaded_reference_images(monkeypatch, tmp_path):
    image_path = tmp_path / "reference.png"
    image_path.write_bytes(b"image")
    uploads = []

    def fake_create_file(**kwargs):
        uploads.append(kwargs["file"].name)
        return SimpleNamespace(id=f"reference-{len(uploads)}")

    monkeypatch.setattr(gemini_module.litellm, "create_file", fake_create_file)
    client = GeminiClient(api_key="test-key", model="gemini-3.1-flash-lite")
    media_file = MediaFile(path=image_path)

    first_upload = client._upload_file(media_file)
    second_upload = client._upload_file(media_file)

    assert first_upload == second_upload
    assert len(uploads) == 1


def test_gemini_client_places_media_labels_before_their_files(monkeypatch, tmp_path):
    video_path = tmp_path / "target.mp4"
    reference_path = tmp_path / "reference.png"
    video_path.write_bytes(b"video")
    reference_path.write_bytes(b"image")
    media_content = []
    upload_index = 0
    retry_options = []

    class RecordingLimiter:
        effective_rpm = 12

        def __init__(self):
            self.calls = []

        def wait(self, estimated_tokens=0):
            self.calls.append(estimated_tokens)

    def fake_create_file(**kwargs):
        nonlocal upload_index
        upload_index += 1
        retry_options.append(kwargs.get("num_retries"))
        return SimpleNamespace(id=f"file-{upload_index}")

    def fake_completion(**kwargs):
        media_content.extend(kwargs["messages"][0]["content"])
        retry_options.append(kwargs.get("num_retries"))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            model_dump=lambda: {},
        )

    monkeypatch.setattr(gemini_module.litellm, "create_file", fake_create_file)
    monkeypatch.setattr(gemini_module.litellm, "completion", fake_completion)
    limiter = RecordingLimiter()
    client = GeminiClient(api_key="test-key", model="gemini-3.1-flash-lite", rate_limiter=limiter)
    message = Message(
        text="Classify the target only.",
        media=[
            MediaFile(path=video_path, label="TARGET VIDEO"),
            MediaFile(path=reference_path, label="REFERENCE STILL ONLY"),
        ],
    )

    response = client.generate(message)

    assert response.text == "ok"
    assert [part.get("text") or part.get("type") for part in media_content] == [
        "TARGET VIDEO",
        "file",
        "REFERENCE STILL ONLY",
        "file",
        "Classify the target only.",
    ]
    assert len(limiter.calls) == 3
    assert client.api_request_count == 3
    assert all(tokens >= 0 for tokens in limiter.calls)
    assert all(retries == 0 for retries in retry_options)


def test_available_clip_prompt_keeps_full_long_description():
    rules = CharacterRules(
        name="Clare",
        series="Claymore",
        clip_root=Path("E:/tmp"),
        personality="Stoic.",
        category_to_action_type={
            "Medium attack": {"action_type": "attack", "tier": "Medium"},
        },
    )
    catalog = ClipCatalog(root=Path("E:/tmp"))
    full_description = (
        "Clare uses awakened blade arms. "
        + "Her fully awakened form has distorted features and massive wings. " * 8
    )
    catalog.clips_by_category["Medium attack"] = [
        ClipEntry(
            Path("E:/tmp/flash slashes.mp4"),
            "flash slashes.mp4",
            "Medium attack",
            full_description,
        ),
    ]

    prompt = build_prompt(
        character_rules=rules,
        opponent_profile=None,
        my_hp=4,
        opponent_hp=4,
        turn_number=4,
        opponent_attacked_last_turn=False,
        opponent_last_attack_tier=None,
        opponent_last_attack_descriptions=[],
        opponent_dialogue=[],
        established_abilities={},
        turn_history=[],
        available_clips=catalog,
    )

    assert full_description in prompt


def test_translated_clip_speech_is_in_current_turn_dialogue_section():
    rules = CharacterRules(
        name="Clare",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Stoic.",
    )
    match_state = SimpleNamespace(
        current_turn=4,
        opponent_dialogue={
            2: ["Old taunt from turn 2."],
            4: ["Current chat taunt."],
        },
    )
    prompt = build_prompt(
        character_rules=rules,
        opponent_profile=None,
        my_hp=4,
        opponent_hp=4,
        turn_number=4,
        opponent_attacked_last_turn=False,
        opponent_last_attack_tier=None,
        opponent_last_attack_descriptions=[],
        opponent_dialogue=match_state.opponent_dialogue[match_state.current_turn],
        established_abilities={},
        turn_history=["Turn 2: Old taunt from turn 2."],
        available_clips=ClipCatalog(root=Path("E:/tmp")),
        opponent_character_name="Aizen",
    )

    dialogue_section = prompt.split("## Opponent's Dialogue This Turn", 1)[1].split(
        "## Full Match Memory", 1
    )[0]
    assert "Current chat taunt." in dialogue_section
    assert "Old taunt from turn 2." not in dialogue_section


def test_whisper_translation_task_is_local_and_toggleable(monkeypatch, tmp_path):
    class FakeWhisper:
        def __init__(self):
            self.task = None

        def transcribe(self, audio_path, **kwargs):
            self.task = kwargs["task"]
            return iter([SimpleNamespace(text=" You are too slow. ")]), None

    model = FakeWhisper()
    monkeypatch.setattr(opponent_analysis.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(opponent_analysis.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(opponent_analysis, "_get_whisper_model", lambda: model)
    monkeypatch.setenv("OPPONENT_STT_ENABLED", "true")
    monkeypatch.setenv("OPPONENT_TRANSCRIPT_TRANSLATION_ENABLED", "true")
    transcript = opponent_analysis._transcribe_media(tmp_path / "clip.mp4")
    assert transcript == "You are too slow."
    assert model.task == "translate"

    monkeypatch.setenv("OPPONENT_TRANSCRIPT_TRANSLATION_ENABLED", "false")
    opponent_analysis._transcribe_media(tmp_path / "clip.mp4")
    assert model.task == "transcribe"


def test_prepare_opponent_media_debug_logs_transcript_for_prompt(monkeypatch):
    events = []

    async def fake_download_clip(url):
        return b"video-bytes"

    monkeypatch.setattr(opponent_analysis, "download_clip", fake_download_clip)
    monkeypatch.setattr(
        opponent_analysis,
        "_transcribe_media",
        lambda path: "Aizen says the outcome was decided long ago.",
    )
    monkeypatch.setattr(
        opponent_analysis,
        "debug_event",
        lambda event, **payload: events.append((event, payload)),
    )
    monkeypatch.setenv("OPPONENT_TRANSCRIPT_TRANSLATION_ENABLED", "true")

    media = asyncio.run(prepare_opponent_media(
        actions=[{
            "attachment_url": "https://example.test/aizen.mp4",
            "filename": "aizen.mp4",
            "action_type": "talk",
        }],
        match_id=10,
        turn=4,
    ))

    transcript_event = next(
        payload for event, payload in events if event == "opponent_transcript_ready_for_prompt"
    )
    assert transcript_event["filename"] == "aizen.mp4"
    assert transcript_event["task"] == "translate"
    assert transcript_event["transcript_preview"] == "Aizen says the outcome was decided long ago."
    assert transcript_event["transcript_truncated"] is False
    opponent_analysis.cleanup_opponent_media(media)


def test_whisper_defaults_to_large_v3_for_local_translation(monkeypatch):
    loaded_models = []

    class FakeWhisperModel:
        def __init__(self, model_name, **kwargs):
            loaded_models.append(model_name)

    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.delenv("OPPONENT_WHISPER_MODEL", raising=False)
    monkeypatch.setattr(opponent_analysis, "_WHISPER_MODEL", None)
    monkeypatch.setattr(opponent_analysis, "_WHISPER_MODEL_NAME", None)

    opponent_analysis._get_whisper_model()

    assert loaded_models == ["large-v3"]
    opponent_analysis.unload_whisper_model()


@pytest.mark.parametrize(("think_setting", "expected_think"), [("true", True), ("false", False)])
def test_ollama_native_request_uses_think_flag_and_sends_images_with_user_message(
    tmp_path,
    monkeypatch,
    think_setting,
    expected_think,
):
    monkeypatch.setenv("OLLAMA_THINK", think_setting)
    image_path = tmp_path / "frame.jpg"
    image_bytes = b"test-frame"
    image_path.write_bytes(image_bytes)
    captured_payload = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"message":{"content":"{}"}}'

    def fake_urlopen(request, timeout):
        captured_payload.update(json.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    model_name = "fredrezones55/Qwen3.5-APEX:latest"
    client = OllamaClient(model=model_name)

    client.generate(Message(
        text="Describe the frame.",
        media=[MediaFile(path=image_path, mime_type="image/jpeg")],
    ))

    assert captured_payload["model"] == model_name
    assert captured_payload["think"] is expected_think
    assert "images" not in captured_payload
    assert captured_payload["messages"][0]["images"] == [base64.b64encode(image_bytes).decode("ascii")]


def test_ollama_frame_budget_is_shared_across_clips():
    budgets = ai_player._allocate_ollama_frame_budgets(video_count=3, total_frame_limit=12)

    assert budgets == [4, 4, 4]
    assert sum(budgets) == 12
    assert ai_player._allocate_ollama_frame_budgets(0, 12) == []
    assert ai_player._estimate_qwen35_frame_tokens(448) == 198
    assert ai_player._estimate_qwen35_frame_tokens(512) == 258


def test_ollama_native_request_uses_configured_generation_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_THINK", "true")
    monkeypatch.setenv("OLLAMA_CONTEXT_SIZE", "32768")
    monkeypatch.setenv("OLLAMA_MAX_OUTPUT_TOKENS", "8192")
    captured_payload = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"message":{"content":"{}"}}'

    def fake_urlopen(request, timeout):
        captured_payload.update(json.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    OllamaClient(model="test-model").generate(Message(text="Return valid JSON."))

    assert captured_payload["think"] is True
    assert captured_payload["options"]["num_ctx"] == 32768
    assert captured_payload["options"]["num_predict"] == 8192


def test_ollama_native_request_reports_empty_final_content(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({
                "message": {"content": "", "thinking": "internal reasoning"},
                "done_reason": "length",
                "eval_count": 2048,
            }).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())

    with pytest.raises(RuntimeError, match="no final message.content.*thinking_chars=18.*done_reason=length"):
        OllamaClient(model="test-model").generate(Message(text="Return JSON."))


def test_aiturndecision_coerces_dict_opponent_analysis_to_strings():
    decision = AITurnDecision.model_validate({
        "reasoning": "I need to summarize the opponent's moves.",
        "actions": [
            {
                "action_type": "attack",
                "tier": "Normal",
                "clip_filename": "normal_attack.mp4",
                "reasoning": "I am attacking here.",
            }
        ],
        "opponent_analysis": [
            {"action": "Intro_4_walks_forward", "description": "The opponent closes distance and prepares to rush."},
            {"action": "NA6_slash_immediate", "analysis": "They slash immediately after the rush."},
        ],
    })

    assert decision.opponent_analysis == [
        "The opponent closes distance and prepares to rush.",
        "They slash immediately after the rush.",
    ]


def test_aiturndecision_extracts_video_description_and_tactical_meaning():
    decision = AITurnDecision.model_validate({
        "reasoning": "I need to summarize the opponent's moves.",
        "actions": [
            {
                "action_type": "defense",
                "tier": "Normal",
                "clip_filename": "block.mp4",
                "reasoning": "I block.",
            }
        ],
        "opponent_analysis": [
            {
                "video_description": "Aizen stops the attack with his finger.",
                "tactical_meaning": "He is testing my reaction speed and controlling the pace.",
            }
        ],
    })

    assert decision.opponent_analysis == [
        "Aizen stops the attack with his finger. He is testing my reaction speed and controlling the pace.",
    ]


def test_opponent_analysis_normalizes_json_strings_and_missing_entries():
    json_analysis = (
        '{"clip_description":"Aizen stops the swing.",'
        '"tactical_meaning":"He is testing Clare\'s reaction."}'
    )

    normalized = ai_player._coerce_opponent_analysis_text(json_analysis)
    complete = ai_player._ensure_opponent_analysis_count([normalized], 2)

    assert complete == [
        "Aizen stops the swing. He is testing Clare's reaction.",
        "No analysis was provided for this clip. Please provide one next time.",
    ]
    assert ai_player._ensure_opponent_analysis_count(["stale output"], 0) == []


def test_extract_opponent_dialogue_reads_talk_actions_only():
    actions = [
        {"action_type": "attack", "dialogue": "Not a talk action."},
        {"action_type": "talk", "dialogue": "  You cannot stop me.  "},
        {"action_type": "talk", "dialogue": "  "},
        {"action_type": "talk", "dialogue": None},
    ]

    assert _extract_opponent_dialogue(actions) == ["You cannot stop me."]


def test_build_ai_state_from_boss_initializes_runtime_dialogue_and_history_fields():
    rules = CharacterRules(
        name="Test Hero",
        series="Example",
        clip_root=Path("E:/tmp"),
        personality="Focused and direct.",
    )
    catalog = ClipCatalog(root=Path("E:/tmp"))
    boss_state = SimpleNamespace(
        match_id=7,
        player1_id=99,
        player2_id=42,
        player1_char_id=1,
        player2_char_id=2,
        player1_hp=4,
        player2_hp=4,
        current_turn=3,
        current_player_id=99,
        pending_attack=None,
        pending_attacker_id=None,
        status="active",
        state_history=[
            {"turn": 1, "player1_hp": 4, "player2_hp": 4},
            {"turn": 2, "player1_hp": 3, "player2_hp": 4},
            {"turn": 3, "player1_hp": 3, "player2_hp": 4},
        ],
    )

    ai_state = _build_ai_state_from_boss(boss_state, rules, catalog)

    assert ai_state.opponent_dialogue == {}
    assert ai_state.opponent_clip_descriptions == {}
    assert ai_state.turn_context_log == []
    assert ai_state.established_abilities == {}
    assert ai_state.used_clips == set()
    assert ai_state.turn_history_log == []


def test_channel_lookup_handles_forum_thread_and_parent_id_variants():
    match_manager = MatchManagerService()
    boss_manager = BossManagerService()

    real_match_id = 123456
    real_fight_id = 654321
    match_manager._active_matches[real_match_id] = SimpleNamespace(match_id=real_match_id)
    boss_manager._active_fights[real_fight_id] = SimpleNamespace(match_id=real_fight_id)

    interaction = SimpleNamespace(
        channel_id=999999,
        channel=SimpleNamespace(id=777777, parent_id=real_match_id),
    )
    assert match_manager.get_match_for_interaction(interaction) is not None

    boss_interaction = SimpleNamespace(
        channel_id=888888,
        channel=SimpleNamespace(id=777777, parent_id=real_fight_id),
    )
    assert boss_manager.get_fight_for_interaction(boss_interaction) is not None
