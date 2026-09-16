from __future__ import annotations

import io
import logging
import random
from pathlib import Path

import discord

from boss.boss_config import BOSS_PLAYER_ID, TIER_FOLDER
from boss.boss_state import BossState
from services.combat_service import end_turn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_clip(path: Path) -> tuple[bytes, str] | None:
    """Read a clip from disk. Returns (bytes, filename) or None on error."""
    try:
        return path.read_bytes(), path.name
    except OSError:
        logger.exception("Failed to read boss clip: %s", path)
        return None


def _pick_random_clip_for_tier(boss_state: BossState, tier: str) -> tuple[bytes, str] | None:
    """Pick a random clip from the standard tier folder. Returns (bytes, filename) or None."""
    clips = boss_state.boss_config.get_clips(tier)
    if not clips:
        return None
    return _read_clip(random.choice(clips))


async def _post_clip(
    channel: discord.abc.Messageable,
    label: str,
    clip_result: tuple[bytes, str] | None,
) -> None:
    """Post a single clip to the channel, or skip silently if clip_result is None."""
    if not clip_result:
        return
    clip_bytes, filename = clip_result
    await channel.send(
        content=label,
        file=discord.File(io.BytesIO(clip_bytes), filename=filename),
    )


# ---------------------------------------------------------------------------
# Match-start hook (called by boss_manager when a fight is created)
# ---------------------------------------------------------------------------

async def run_boss_intro(
    boss_state: BossState,
    channel: discord.abc.Messageable,
) -> None:
    """Post the boss's intro clip (if any). Called once when the fight starts."""
    script = boss_state.boss_config.get_script()
    intro_path = script.on_match_start(boss_state)
    if intro_path:
        clip = _read_clip(intro_path)
        await _post_clip(
            channel,
            f"⚔️ **{boss_state.boss_config.display_name}** appears!",
            clip,
        )


# ---------------------------------------------------------------------------
# Main boss turn
# ---------------------------------------------------------------------------

async def run_boss_turn(
    boss_state: BossState,
    channel: discord.abc.Messageable,
) -> dict | None:
    """Execute the boss's full turn automatically and post results to the channel.

    Flow:
    1.  on_turn_start  → optional flavour clip before the action.
    2.  should_defend  → if True, inject a defense action.
    3.  should_attack  → if True, pick tier + clip and inject an attack action.
    4.  end_turn       → resolve combat via the normal engine.
    5.  Post attack clip(s) and turn embed.
    6.  on_turn_end    → optional flavour clip after the embed.
    7.  on_boss_hit / on_player_hit → reaction clips based on damage outcome.

    Returns the turn_summary dict, or None if the turn could not run.
    """
    if not boss_state.is_boss_turn:
        logger.error("run_boss_turn called but it is not the boss's turn.")
        return None

    if boss_state.status != "active":
        return None

    config = boss_state.boss_config
    script = config.get_script()

    # ── Step 1: Pre-turn flavour clip ─────────────────────────────────────────
    turn_start_path = script.on_turn_start(boss_state)
    if turn_start_path:
        clip = _read_clip(turn_start_path)
        await _post_clip(channel, f"*{config.display_name} stirs...*", clip)

    # ── Step 2: Defense ───────────────────────────────────────────────────────
    if script.should_defend(boss_state):
        defense_tier = script.pick_defense_tier(boss_state)
        defense_path = script.pick_defense_clip(boss_state, defense_tier)

        if defense_path:
            defense_clip = _read_clip(defense_path)
        else:
            # Fall back to a random clip from the standard defense folder.
            defense_folder = config.clips_dir / "defenses" / TIER_FOLDER.get(defense_tier, defense_tier.lower())
            clips = [
                p for p in defense_folder.iterdir()
                if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
            ] if defense_folder.exists() else []
            defense_clip = _read_clip(random.choice(clips)) if clips else None

        defense_filename = defense_clip[1] if defense_clip else f"boss_defense_{defense_tier.lower()}.mp4"
        boss_state.current_turn_actions.append({
            "action_type": "defense",
            "tier": defense_tier,
            "attachment_url": None,
            "filename": defense_filename,
        })
        if defense_clip:
            boss_state.video_cache.setdefault(BOSS_PLAYER_ID, []).append({
                "bytes": defense_clip[0],
                "label": "DEFENSE",
                "filename": defense_filename,
            })

    # ── Step 3: Attack ────────────────────────────────────────────────────────
    if script.should_attack(boss_state):
        tier = script.pick_tier(boss_state)
        attack_path = script.pick_attack_clip(boss_state, tier)

        if attack_path:
            attack_clip = _read_clip(attack_path)
        else:
            attack_clip = _pick_random_clip_for_tier(boss_state, tier)

        attack_filename = attack_clip[1] if attack_clip else f"boss_attack_{tier.lower()}.mp4"
        boss_state.current_turn_actions.append({
            "action_type": "attack",
            "tier": tier,
            "attachment_url": None,
            "filename": attack_filename,
        })
        if attack_clip:
            boss_state.video_cache.setdefault(BOSS_PLAYER_ID, []).append({
                "bytes": attack_clip[0],
                "label": "ATTACK",
                "filename": attack_filename,
            })

    # ── Step 4: End the boss's turn via the normal engine ─────────────────────
    success, message, turn_summary = await end_turn(boss_state, BOSS_PLAYER_ID)
    if not success:
        logger.error("Boss end_turn failed: %s", message)
        return None

    # ── Step 5: Post boss clips and turn embed ────────────────────────────────
    cached: list[dict] = boss_state.video_cache.pop(BOSS_PLAYER_ID, [])
    total = len(cached)
    for i, clip in enumerate(cached, start=1):
        await channel.send(
            content=f"📹 **{config.display_name}** *(Clip {i}/{total})*",
            file=discord.File(io.BytesIO(clip["bytes"]), filename=clip["filename"]),
        )

    if not cached and script.should_attack(boss_state):
        logger.warning(
            "Boss '%s' attacked with no clip available.", config.slug
        )
        await channel.send(
            content=f"👊 **{config.display_name}** launches an attack!"
        )
    # ── Step 6: Post-turn flavour clip ────────────────────────────────────────
    turn_end_path = script.on_turn_end(boss_state)
    if turn_end_path:
        clip = _read_clip(turn_end_path)
        await _post_clip(channel, "", clip)

    # ── Step 7: Reaction clips based on damage outcome ────────────────────────
    resolution = turn_summary.get("resolution")  # damage the boss took this turn
    attack_sent = turn_summary.get("attack_sent")  # outgoing attack (boss hit the player)

    # resolution = incoming attack that resolved against the boss this turn
    if resolution and resolution.get("damage", 0) > 0:
        boss_damage = resolution["damage"]
        reaction_path = script.on_boss_hit(boss_state, boss_damage)
        if reaction_path:
            clip = _read_clip(reaction_path)
            await _post_clip(channel, "", clip)

    # attack_sent = the boss's outgoing attack — we can estimate damage from HP delta.
    # The simplest approach: check if player HP dropped compared to what it was before.
    # We stored p1_hp in the summary so we can compare against boss_state.player1_hp.
    p1_hp_after = turn_summary.get("p1_hp", boss_state.player1_hp)
    # We don't have hp_before in the summary, but attack_sent tier implies damage.
    if attack_sent:
        tier_damage = {"Normal": 1, "Medium": 2, "Absolute": 3, "Over-Absolute": 4}
        player_damage = tier_damage.get(attack_sent.get("tier", ""), 0)
        if player_damage > 0:
            taunt_path = script.on_player_hit(boss_state, player_damage)
            if taunt_path:
                clip = _read_clip(taunt_path)
                await _post_clip(channel, "", clip)

    return turn_summary
