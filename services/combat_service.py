from __future__ import annotations

import logging
import aiohttp
import asyncio
import discord

from services.match_manager_service import MatchState
from database.models.character import Character

logger = logging.getLogger(__name__)

DAMAGE_MAP = {
    "Normal": 1,
    "Medium": 2,
    "Absolute": 3,
    "Over-Absolute": 4,
}

# ---------------------------------------------------------------------------
# Sequential turn model
# ---------------------------------------------------------------------------
#
# One player acts at a time. The active player is match_state.current_player_id.
# They submit actions (attack, defense, custom) in any order, then /end_turn.
#
# Pending attack resolution (happens at the START of a turn, before anything):
#   - If match_state.pending_attack is set, it means the previous player attacked.
#   - The new active player's FIRST submitted action determines the outcome:
#       • defense with tier >= attack tier  → blocked (0 damage)
#       • defense with tier <  attack tier  → failed (full attack damage)
#       • anything else (attack/custom)     → no defense (full attack damage)
#   - Damage is applied immediately when the first action is submitted.
#   - The pending_attack is then cleared. The turn continues normally.
#
# After /end_turn:
#   - If the active player attacked this turn → their attack is stored as
#     pending_attack for the opponent's upcoming turn.
#   - Turn ownership flips to the opponent.
#
# Custom actions: clips are stored in video_cache, never affect damage.
# end_turn: always valid, always independent of the action sequence.
#
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _opponent(match_state: MatchState, player_id: int) -> int:
    return (
        match_state.player2_id
        if player_id == match_state.player1_id
        else match_state.player1_id
    )


def _player_hp(match_state: MatchState, player_id: int) -> int:
    return (
        match_state.player1_hp
        if player_id == match_state.player1_id
        else match_state.player2_hp
    )


def _set_player_hp(match_state: MatchState, player_id: int, hp: int) -> None:
    if player_id == match_state.player1_id:
        match_state.player1_hp = hp
    else:
        match_state.player2_hp = hp


def _resolve_pending_attack(
    match_state: MatchState,
    first_action: dict,
) -> dict:
    """Resolve the pending attack against the defender's first action.

    Returns a resolution dict describing what happened. HP is updated
    in-place on match_state.
    """
    pending = match_state.pending_attack          # guaranteed non-None here
    attacker_id = match_state.pending_attacker_id
    defender_id = match_state.current_player_id
    att_tier = pending["tier"]
    att_val = DAMAGE_MAP.get(att_tier, 1)

    if first_action["action_type"] == "defense":
        def_val = DAMAGE_MAP.get(first_action["tier"], 0)
        if def_val >= att_val:
            # Successful block.
            outcome = "blocked"
            damage = 0
        else:
            # Defense attempted but tier too low — full damage.
            outcome = "failed_defense"
            damage = att_val
    else:
        # First action was not a defense — attack lands uncontested.
        outcome = "no_defense"
        damage = att_val

    # Apply damage to defender.
    current_hp = _player_hp(match_state, defender_id)
    new_hp = max(0, current_hp - damage)
    _set_player_hp(match_state, defender_id, new_hp)

    # Clear the pending attack — it has resolved.
    match_state.pending_attack = None
    match_state.pending_attacker_id = None

    return {
        "attacker_id": attacker_id,
        "defender_id": defender_id,
        "attack_tier": att_tier,
        "defense_action": first_action if first_action["action_type"] == "defense" else None,
        "outcome": outcome,   # "blocked" | "failed_defense" | "no_defense"
        "damage": damage,
        "defender_hp_after": new_hp,
    }


async def _download(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception:
        logger.exception("Failed to download attachment: %s", url)
    return None


# ---------------------------------------------------------------------------
# Public API — called by the battle cog
# ---------------------------------------------------------------------------

def get_active_player(match_state: MatchState) -> int:
    """Return the ID of the player whose turn it currently is."""
    return match_state.current_player_id


def has_pending_attack(match_state: MatchState) -> bool:
    return match_state.pending_attack is not None


async def record_action(
    match_state: MatchState,
    player_id: int,
    action_type: str,        # "attack" | "defense" | "custom"
    tier: str | None,        # None for custom actions
    attachment: discord.Attachment,
) -> tuple[bool, str, dict | None]:
    """Submit one action for the active player.

    Returns (success, message, resolution_dict).
    resolution_dict is non-None only when a pending attack resolves on this action
    (i.e. this is the player's first action and there was an incoming attack).
    """
    # ── Guard: only the active player may act ────────────────────────────────
    if player_id != match_state.current_player_id:
        return False, "It's not your turn.", None

    if match_state.is_paused:
        return False, "⏸️ This match has been paused by a referee. Wait for them to resolve the objection.", None

    if not attachment.filename.lower().endswith(('.mp4', '.mov', '.webm', '.mkv')):
        return False, "Please upload a valid video file (.mp4, .mov, .webm, .mkv).", None

    # ── Guard: at most one attack per turn ───────────────────────────────────
    if action_type == "attack" and any(
        a["action_type"] == "attack" for a in match_state.current_turn_actions
    ):
        return False, "You can only attack once per turn. You can still add defense or custom actions.", None

    # ── Build the action entry ────────────────────────────────────────────────
    action = {
        "action_type": action_type,
        "tier": tier,
        "attachment_url": attachment.url,
        "filename": attachment.filename,
    }

    # ── Resolve pending attack on FIRST action ────────────────────────────────
    resolution = None
    is_first_action = len(match_state.current_turn_actions) == 0
    if is_first_action and match_state.pending_attack is not None:
        resolution = _resolve_pending_attack(match_state, action)

    # ── Append action to the turn ─────────────────────────────────────────────
    match_state.current_turn_actions.append(action)

    # ── Download and cache the clip ───────────────────────────────────────────
    clip_bytes = await _download(attachment.url)
    if clip_bytes is not None:
        label = action["action_type"].upper() + (f" ({action['tier']})" if action.get("tier") else "")
        match_state.video_cache.setdefault(player_id, []).append({
            "bytes": clip_bytes,
            "label": label,
            "filename": attachment.filename,
        })

    # ── Build feedback message ────────────────────────────────────────────────
    summary = " → ".join(
        f"{a['action_type'].upper()}" + (f" ({a['tier']})" if a["tier"] else "")
        for a in match_state.current_turn_actions
    )
    msg = f"✅ **{summary}**. Keep going or `/end_turn` when done."

    if resolution:
        outcome = resolution["outcome"]
        dmg = resolution["damage"]
        if outcome == "blocked":
            msg = f"🛡️ **Blocked** the incoming {resolution['attack_tier']} attack! " + msg
        elif outcome == "failed_defense":
            msg = (
                f"💥 Defense failed — took **{dmg} damage** from the {resolution['attack_tier']} attack! "
                + msg
            )
        else:
            msg = (
                f"💥 No defense — took **{dmg} damage** from the {resolution['attack_tier']} attack! "
                + msg
            )

    return True, msg, resolution


async def end_turn(
    match_state: MatchState,
    player_id: int,
) -> tuple[bool, str, dict | None]:
    """End the active player's turn.

    Returns (success, message, turn_summary_dict).
    turn_summary_dict is non-None when the turn actually ended (i.e. it was
    the correct player calling end_turn). The cog uses it to build the embed.

    Special case: if the active player has a pending attack against them but
    has submitted NO actions yet, ending their turn counts as "no defense" —
    the pending attack resolves at this point for full damage.
    """
    if player_id != match_state.current_player_id:
        return False, "It's not your turn.", None

    if match_state.is_paused:
        return False, "⏸️ This match has been paused by a referee. Wait for them to resolve the objection.", None

    # ── Resolve pending attack if player ends turn without any actions ────────
    resolution = match_state.last_resolution  # already set if they submitted actions
    if match_state.pending_attack is not None and resolution is None:
        # Player passed with no actions at all — treat as no_defense.
        fake_pass_action = {"action_type": "pass", "tier": None}
        resolution = _resolve_pending_attack(match_state, fake_pass_action)

    # ── Store this turn's attack as pending for the opponent ──────────────────
    attack_this_turn = next(
        (a for a in match_state.current_turn_actions if a["action_type"] == "attack"),
        None,
    )
    if attack_this_turn is not None:
        match_state.pending_attack = attack_this_turn
        match_state.pending_attacker_id = player_id
    else:
        # No attack this turn — clear any stale pending (shouldn't happen, but safe).
        match_state.pending_attack = None
        match_state.pending_attacker_id = None

    # ── Check for winner ──────────────────────────────────────────────────────
    winner_id = None
    loser_id = None
    opp_id = _opponent(match_state, player_id)

    p1_hp = match_state.player1_hp
    p2_hp = match_state.player2_hp

    if p1_hp <= 0 and p2_hp <= 0:
        pass  # draw
    elif p1_hp <= 0:
        winner_id = match_state.player2_id
        loser_id = match_state.player1_id
        match_state.status = "finished"
    elif p2_hp <= 0:
        winner_id = match_state.player1_id
        loser_id = match_state.player2_id
        match_state.status = "finished"

    # ── Build the turn summary for the embed ──────────────────────────────────
    turn_summary = {
        "acting_player_id": player_id,
        "actions": list(match_state.current_turn_actions),
        "resolution": resolution,           # how the incoming attack resolved (or None)
        "attack_sent": attack_this_turn,    # the outgoing attack (or None)
        "winner_id": winner_id,
        "loser_id": loser_id,
        "p1_hp": match_state.player1_hp,
        "p2_hp": match_state.player2_hp,
        "p1_id": match_state.player1_id,
        "p2_id": match_state.player2_id,
    }

    # ── Reset turn state and pass to opponent ─────────────────────────────────
    match_state.current_turn_actions = []
    match_state.last_resolution = None
    match_state.current_turn += 1
    match_state.current_player_id = opp_id

    # Snapshot the state after this turn completes (used by referee rollback).
    match_state._snapshot()

    return True, "Turn ended.", turn_summary


# ---------------------------------------------------------------------------
# Embed generation
# ---------------------------------------------------------------------------

async def generate_turn_embed(
    match_state: MatchState,
    turn_summary: dict,
) -> discord.Embed:
    """Build a Discord embed describing the completed turn."""
    p1 = await Character.get_or_none(claimed_by_id=match_state.player1_id)
    p2 = await Character.get_or_none(claimed_by_id=match_state.player2_id)
    p1_name = p1.character_name if p1 else "Player 1"
    p2_name = p2.character_name if p2 else "Player 2"

    acting_id = turn_summary["acting_player_id"]
    actions: list[dict] = turn_summary["actions"]
    resolution: dict | None = turn_summary["resolution"]
    attack_sent: dict | None = turn_summary["attack_sent"]
    winner_id = turn_summary["winner_id"]

    # Embed colour: red if damage dealt, gold if match over, green otherwise.
    damage_dealt = resolution["damage"] > 0 if resolution else False
    if winner_id:
        colour = discord.Color.gold()
    elif damage_dealt:
        colour = discord.Color.red()
    else:
        colour = discord.Color.green()

    embed = discord.Embed(
        title=f"⚔️ Turn {match_state.current_turn - 1}",
        color=colour,
    )

    # ── Actions taken ─────────────────────────────────────────────────────────
    def fmt_action(a: dict) -> str:
        tier = f" ({a['tier']})" if a.get("tier") else ""
        icons = {"attack": "⚔️", "defense": "🛡️", "custom": "✨", "pass": "⏭️"}
        icon = icons.get(a["action_type"], "•")
        return f"{icon} {a['action_type'].capitalize()}{tier}"

    if actions:
        actions_text = " → ".join(fmt_action(a) for a in actions)
    else:
        actions_text = "*(passed — no actions)*"

    embed.add_field(
        name=f"<@{acting_id}>'s Turn",
        value=actions_text,
        inline=False,
    )

    # ── Incoming attack resolution ────────────────────────────────────────────
    if resolution is not None:
        att_id = resolution["attacker_id"]
        def_id = resolution["defender_id"]
        att_tier = resolution["attack_tier"]
        outcome = resolution["outcome"]
        dmg = resolution["damage"]

        if outcome == "blocked":
            def_tier = resolution["defense_action"]["tier"]
            res_text = (
                f"<@{att_id}>'s **{att_tier}** attack → "
                f"🛡️ **BLOCKED** by <@{def_id}>'s **{def_tier}** defense! *(0 damage)*"
            )
        elif outcome == "failed_defense":
            def_tier = resolution["defense_action"]["tier"]
            res_text = (
                f"<@{att_id}>'s **{att_tier}** attack → "
                f"❌ <@{def_id}>'s **{def_tier}** defense was too weak! "
                f"💥 **{dmg} damage**"
            )
        else:  # no_defense or pass
            res_text = (
                f"<@{att_id}>'s **{att_tier}** attack → "
                f"💥 **{dmg} damage** to <@{def_id}>! *(no defense)*"
            )

        embed.add_field(
            name="⚡ Attack Resolution",
            value=res_text,
            inline=False,
        )

    # ── Outgoing attack notice ────────────────────────────────────────────────
    if attack_sent is not None and match_state.status != "finished":
        opp_id = _opponent(match_state, acting_id)
        embed.add_field(
            name="🔮 Pending Attack",
            value=(
                f"⏳ <@{acting_id}> sent a **{attack_sent['tier']}** attack — "
                f"<@{opp_id}> must defend with it as their **first action** next turn!"
            ),
            inline=False,
        )

    # ── HP bars ───────────────────────────────────────────────────────────────
    def hp_bar(hp: int) -> str:
        return f"`{'❤️' * hp}{'🖤' * (4 - hp)}`  ({hp}/4)"

    embed.add_field(
        name="HP",
        value=(
            f"<@{match_state.player1_id}> ({p1_name}): {hp_bar(turn_summary['p1_hp'])}\n"
            f"<@{match_state.player2_id}> ({p2_name}): {hp_bar(turn_summary['p2_hp'])}"
        ),
        inline=False,
    )

    # ── Winner ────────────────────────────────────────────────────────────────
    if winner_id:
        embed.add_field(
            name="🏆 MATCH OVER",
            value=f"**Winner:** <@{winner_id}>! Congratulations!",
            inline=False,
        )

    return embed
