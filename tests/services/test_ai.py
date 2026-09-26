from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from boss.boss_manager import BossManagerService
from boss.scripts.clare import ClareBossScript, _build_ai_state_from_boss, _extract_opponent_dialogue
from services.ai import RateLimiter
from services.ai.ai_match_state import AIMatchState
from services.ai.character_rules import CharacterRules
from services.ai.clip_catalog import ClipCatalog, ClipEntry
from services.match.match_manager_service import MatchManagerService


def test_rate_limiter_applies_safety_margin_to_tpm_and_rpm():
    rate_limiter = RateLimiter(tpm_limit=65000, rpm_limit=15, safety_margin=0.8)

    assert rate_limiter.effective_rpm == 12
    assert rate_limiter.effective_tpm == 52000


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

    assert any("Opponent did" in entry for entry in state.turn_history_log)
    assert any("I responded" in entry for entry in state.turn_history_log)
    assert state.latest_opponent_descriptions() == [
        "The opponent lunges forward with a rising slash from the left flank."
    ]
    assert state.latest_opponent_dialogue() == ["You are too slow."]
    assert state.turn_context_log[0]["opponent_dialogue"] == ["You are too slow."]
    assert 'Opponent said: "You are too slow."' in state.turn_history_log[0]


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
