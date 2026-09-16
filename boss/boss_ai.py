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


def _inject_attack(boss_state: BossState, tier: str, attack_clip: tuple[bytes, str] | None) -> None:
    """Append an attack action to boss_state and cache the clip bytes."""
    filename = attack_clip[1] if attack_clip else f"boss_attack_{tier.lower()}.mp4"
    boss_state.current_turn_actions.append({
        "action_type": "attack",
        "tier": tier,
        "attachment_url": None,
        "filename": filename,
    })
    if attack_clip:
        boss_state.video_cache.setdefault(BOSS_PLAYER_ID, []).append({
            "bytes": attack_clip[0],
            "label": "ATTACK",
            "filename": filename,
        })


# ---------------------------------------------------------------------------
# Match-start hook (called by boss_battle cog when a fight is created)
# ---------------------------------------------------------------------------

async def run_boss_intro(
    boss_state: BossState,
    channel: discord.abc.Messageable,
) -> None:
    """Post the boss's intro clip (if any). Called once when the fight starts."""
    script = boss_state.script
    intro_path = script.on_match_start(boss_state)
    if intro_path:
        clip = _read_clip(intro_path)
        await _post_clip(
            channel,
            f"⚔️ **{boss_state.boss_config.display_name}** appears!",
            clip,
        )


# ---------------------------------------------------------------------------
# Shared post-turn logic (respawn, reactions, win/loss)
# ---------------------------------------------------------------------------

async def _handle_post_turn(
    boss_state: BossState,
    channel: discord.abc.Messageable,
    turn_summary: dict,
    post_clip_path: Path | None,
) -> dict:
    """Handle everything after the attack clips and embed are posted.

    Posts the post_clip (if any), checks respawn, posts reaction/taunt clips,
    and handles victory/defeat clips.

    Returns turn_summary (possibly modified if a respawn occurred).
    """
    config = boss_state.boss_config
    script = boss_state.script

    # ── Post-attack RP clip ───────────────────────────────────────────────────
    if post_clip_path:
        clip = _read_clip(post_clip_path)
        await _post_clip(channel, "", clip)

    # ── Respawn check ─────────────────────────────────────────────────────────
    if turn_summary["winner_id"] == boss_state.player1_id:
        respawn_path = script.try_respawn(boss_state)
        if respawn_path:
            new_hp = script.respawn_hp(boss_state)
            boss_state.boss_hp = new_hp
            boss_state.status = "active"
            boss_state.winner_id = None
            boss_state.loser_id = None

            respawn_clip = _read_clip(respawn_path)
            await _post_clip(
                channel,
                f"💀 **{config.display_name}** has fallen... but something stirs.",
                respawn_clip,
            )
            await channel.send(
                f"🔄 **{config.display_name} has been reborn!** "
                f"HP restored to **{new_hp}**.\n"
                f"▶️ **Turn {boss_state.current_turn}** — <@{boss_state.player1_id}>'s move!"
            )
            turn_summary = dict(turn_summary)
            turn_summary["winner_id"] = None
            turn_summary["loser_id"] = None
            return turn_summary

    # ── Victory / defeat clips ────────────────────────────────────────────────
    if turn_summary.get("winner_id"):
        if turn_summary["winner_id"] == BOSS_PLAYER_ID:
            victory_path = script.on_victory(boss_state)
            if victory_path:
                clip = _read_clip(victory_path)
                await _post_clip(channel, f"💀 **{config.display_name}** stands victorious.", clip)
        else:
            defeat_path = script.on_defeat(boss_state)
            if defeat_path:
                clip = _read_clip(defeat_path)
                await _post_clip(channel, f"🏆 **{config.display_name}** has been defeated!", clip)

    return turn_summary


# ---------------------------------------------------------------------------
# Main boss turn
# ---------------------------------------------------------------------------

async def run_boss_turn(
    boss_state: BossState,
    channel: discord.abc.Messageable,
) -> dict | None:
    """Execute the boss's full turn automatically and post results to the channel.

    If the script overrides plan_turn(), uses the scripted path:
      pre_clip → attack → embed → post_clip → respawn/win/loss

    Otherwise falls back to the original hook-based path:
      on_turn_start → should_defend → pick_tier → end_turn → embed →
      on_turn_end → respawn → on_boss_hit/on_player_hit → win/loss

    Returns the turn_summary dict (winner_id cleared if respawned), or None.
    """
    if not boss_state.is_boss_turn:
        logger.error("run_boss_turn called but it is not the boss's turn.")
        return None

    if boss_state.status != "active":
        return None

    config = boss_state.boss_config
    script = boss_state.script

    from services.combat_service import generate_turn_embed

    # =========================================================================
    # SCRIPTED PATH — boss overrides plan_turn()
    # =========================================================================
    if script.uses_plan_turn():
        pre_clip_path, tier, attack_clip_path, post_clip_path = script.plan_turn(boss_state)

        # Pre-attack RP clip.
        if pre_clip_path:
            pre_clip = _read_clip(pre_clip_path)
            await _post_clip(channel, f"*{config.display_name}...*", pre_clip)

        # Resolve attack clip.
        if attack_clip_path:
            attack_clip = _read_clip(attack_clip_path)
        else:
            attack_clip = _pick_random_clip_for_tier(boss_state, tier)

        _inject_attack(boss_state, tier, attack_clip)

        # End the boss's turn via the combat engine.
        success, message, turn_summary = await end_turn(boss_state, BOSS_PLAYER_ID)
        if not success:
            logger.error("Boss end_turn failed: %s", message)
            return None

        # Post the attack clip.
        cached: list[dict] = boss_state.video_cache.pop(BOSS_PLAYER_ID, [])
        for i, clip in enumerate(cached, start=1):
            await channel.send(
                content=f"📹 **{config.display_name}**",
                file=discord.File(io.BytesIO(clip["bytes"]), filename=clip["filename"]),
            )
        if not cached:
            logger.warning("Boss '%s' had no attack clip for tier '%s'.", config.slug, tier)
            await channel.send(content=f"👊 **{config.display_name}** launches an attack!")

        # Post the turn embed.
        embed = await generate_turn_embed(boss_state, turn_summary)
        await channel.send(content=f"⚔️ **{config.display_name}** ended their turn.", embed=embed)

        return await _handle_post_turn(boss_state, channel, turn_summary, post_clip_path)

    # =========================================================================
    # HOOK-BASED PATH — base class / non-scripted boss
    # =========================================================================

    # Step 1: Pre-turn flavour clip.
    turn_start_path = script.on_turn_start(boss_state)
    if turn_start_path:
        clip = _read_clip(turn_start_path)
        await _post_clip(channel, f"*{config.display_name} stirs...*", clip)

    # Step 2: Defense.
    if script.should_defend(boss_state):
        defense_tier = script.pick_defense_tier(boss_state)
        defense_path = script.pick_defense_clip(boss_state, defense_tier)

        if defense_path:
            defense_clip = _read_clip(defense_path)
        else:
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

    # Step 3: Attack.
    if script.should_attack(boss_state):
        tier = script.pick_tier(boss_state)
        attack_path = script.pick_attack_clip(boss_state, tier)
        attack_clip = _read_clip(attack_path) if attack_path else _pick_random_clip_for_tier(boss_state, tier)
        _inject_attack(boss_state, tier, attack_clip)

    # Step 4: End turn.
    success, message, turn_summary = await end_turn(boss_state, BOSS_PLAYER_ID)
    if not success:
        logger.error("Boss end_turn failed: %s", message)
        return None

    # Step 5: Post clips.
    cached = boss_state.video_cache.pop(BOSS_PLAYER_ID, [])
    total = len(cached)
    for i, clip in enumerate(cached, start=1):
        await channel.send(
            content=f"📹 **{config.display_name}** *(Clip {i}/{total})*",
            file=discord.File(io.BytesIO(clip["bytes"]), filename=clip["filename"]),
        )
    if not cached and script.should_attack(boss_state):
        logger.warning("Boss '%s' attacked with no clip available.", config.slug)
        await channel.send(content=f"👊 **{config.display_name}** launches an attack!")

    # Step 6: Turn embed.
    embed = await generate_turn_embed(boss_state, turn_summary)
    await channel.send(content=f"⚔️ **{config.display_name}** ended their turn.", embed=embed)

    # Step 7: Post-turn flavour.
    turn_end_path = script.on_turn_end(boss_state)

    # Step 8-10: Respawn, reactions, win/loss.
    turn_summary = await _handle_post_turn(boss_state, channel, turn_summary, turn_end_path)

    # Reaction clips (hook-based path only).
    if not turn_summary.get("winner_id"):
        resolution = turn_summary.get("resolution")
        attack_sent = turn_summary.get("attack_sent")

        if resolution and resolution.get("damage", 0) > 0:
            reaction_path = script.on_boss_hit(boss_state, resolution["damage"])
            if reaction_path:
                await _post_clip(channel, "", _read_clip(reaction_path))

        if attack_sent:
            tier_damage = {"Normal": 1, "Medium": 2, "Absolute": 3, "Over-Absolute": 4}
            player_damage = tier_damage.get(attack_sent.get("tier", ""), 0)
            if player_damage > 0:
                taunt_path = script.on_player_hit(boss_state, player_damage)
                if taunt_path:
                    await _post_clip(channel, "", _read_clip(taunt_path))

    return turn_summary
