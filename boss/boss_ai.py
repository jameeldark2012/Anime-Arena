from __future__ import annotations

import io
import logging
import random

import discord

from boss.boss_config import BOSS_PLAYER_ID, TIER_FOLDER
from boss.boss_state import BossState
from services.combat_service import end_turn

logger = logging.getLogger(__name__)


def _pick_tier(boss_state: BossState) -> str:
    """Randomly select an attack tier according to the boss's configured weights.

    Only tiers that have at least one clip available are eligible.
    Falls back to any tier with a weight if no clips exist (so the fight
    can still proceed even before clips are dropped in).
    """
    config = boss_state.boss_config
    tiers = list(config.attack_weights.keys())
    weights = [config.attack_weights[t] for t in tiers]

    # Prefer tiers that actually have clips ready.
    tiers_with_clips = [t for t in tiers if config.get_clips(t)]
    if tiers_with_clips:
        weights_with_clips = [config.attack_weights[t] for t in tiers_with_clips]
        return random.choices(tiers_with_clips, weights=weights_with_clips, k=1)[0]

    # No clips at all — still pick a tier so the game state advances.
    logger.warning(
        "Boss '%s' has no clips in any tier folder. Boss will attack without a video.",
        config.slug,
    )
    return random.choices(tiers, weights=weights, k=1)[0]


def _pick_clip(boss_state: BossState, tier: str) -> tuple[bytes, str] | None:
    """Pick a random clip file for the given tier.

    Returns (file_bytes, filename) or None if no clips are available.
    """
    clips = boss_state.boss_config.get_clips(tier)
    if not clips:
        return None
    clip_path = random.choice(clips)
    try:
        return clip_path.read_bytes(), clip_path.name
    except OSError:
        logger.exception("Failed to read boss clip: %s", clip_path)
        return None


async def run_boss_turn(
    boss_state: BossState,
    channel: discord.abc.Messageable,
) -> dict | None:
    """Execute the boss's full turn automatically and post results to the channel.

    This is called by the boss_battle cog immediately after the human player
    calls /end_turn. The function:

    1. Resolves any pending incoming attack (boss takes damage / blocks if it
       ever defends — for now boss_never_defends means it just skips defense
       and the engine handles the damage naturally via end_turn with no actions).
    2. If the boss is not dead, picks a tier, picks a clip, injects an attack
       action directly into the match state, then calls end_turn.
    3. Posts the boss's clip(s) and the turn embed to the channel.

    Returns the turn_summary dict from end_turn, or None if the boss turn
    could not be executed (e.g. wrong player turn, match already finished).
    """
    if not boss_state.is_boss_turn:
        logger.error("run_boss_turn called but it is not the boss's turn.")
        return None

    if boss_state.status != "active":
        return None

    config = boss_state.boss_config

    # ── Step 1: Handle incoming pending attack ────────────────────────────────
    # If the human player attacked last turn, the boss needs to "respond."
    # Since boss_never_defends=True, the boss takes full damage by doing nothing
    # before its attack. We handle this by injecting a no-op first action only
    # if the boss is configured to never defend AND there's a pending attack.
    # Actually — the combat engine already handles this: if we call end_turn
    # with no actions submitted, it treats it as no_defense and applies full
    # damage. So we don't need to inject anything for the defense phase.
    # We only inject the attack action below.

    # ── Step 2: Check if boss is already dead (pending attack may have killed it) ──
    # We'll call end_turn once with just the attack (or no attack if dead).
    # The engine's end_turn resolves the pending attack on its own if no actions
    # were submitted. But we want the boss to attack IN THE SAME TURN.
    # So: inject the attack first, then call end_turn — the engine will resolve
    # the pending defense + record the outgoing attack in one shot.

    # Pick attack tier and clip.
    tier = _pick_tier(boss_state)
    clip_result = _pick_clip(boss_state, tier)

    # Inject the boss's attack directly into the turn actions list.
    # We bypass record_action (which expects a discord.Attachment) and
    # write directly to the state — this is intentional for the AI path.
    clip_filename = clip_result[1] if clip_result else f"boss_attack_{tier.lower()}.mp4"
    attack_action = {
        "action_type": "attack",
        "tier": tier,
        "attachment_url": None,   # no CDN URL — clip is served from disk
        "filename": clip_filename,
    }
    boss_state.current_turn_actions.append(attack_action)

    # Cache the clip bytes so end_turn's video pipeline can post it.
    if clip_result:
        clip_bytes, clip_filename = clip_result
        boss_state.video_cache.setdefault(BOSS_PLAYER_ID, []).append({
            "bytes": clip_bytes,
            "label": "ATTACK",
            "filename": clip_filename,
        })

    # ── Step 3: End the boss's turn via the normal engine ────────────────────
    success, message, turn_summary = await end_turn(boss_state, BOSS_PLAYER_ID)
    if not success:
        logger.error("Boss end_turn failed: %s", message)
        return None

    # ── Step 4: Post boss clips to the channel ────────────────────────────────
    cached: list[dict] = boss_state.video_cache.pop(BOSS_PLAYER_ID, [])
    total = len(cached)
    for i, clip in enumerate(cached, start=1):
        await channel.send(
            content=f"👊 **{config.display_name}** attacks! *(Clip {i}/{total})*",
            file=discord.File(io.BytesIO(clip["bytes"]), filename=clip["filename"]),
        )

    if not cached:
        # No clips available — still narrate the attack.
        await channel.send(
            content=f"👊 **{config.display_name}** launches a **{tier}** attack!"
        )

    return turn_summary
