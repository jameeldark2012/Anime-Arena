from __future__ import annotations

import asyncio
import io
from typing import Callable, Awaitable

import discord
from discord.ext import commands

from services.combat_service import record_action
from core.config import settings

# How long (seconds) temporary public messages linger before auto-deletion.
TEMP_MSG_TTL = 30
# Timeout (seconds) to wait for the user to upload their clip.
CLIP_UPLOAD_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Auto-delete helper
# ---------------------------------------------------------------------------

def auto_delete(msg: discord.Message, delay: float = TEMP_MSG_TTL) -> None:
    """Schedule a message for deletion after `delay` seconds. Fire-and-forget."""
    async def _delete() -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
    asyncio.create_task(_delete())


# ---------------------------------------------------------------------------
# Referee ping helper
# ---------------------------------------------------------------------------

def ref_ping() -> str:
    """Return a formatted referee ping string."""
    return f"<@&{settings.REFEREE_ROLE_ID}>" if settings.REFEREE_ROLE_ID else "@here"


# ---------------------------------------------------------------------------
# Clip collection helper
# ---------------------------------------------------------------------------

async def collect_clip(
    bot: commands.Bot,
    interaction: discord.Interaction,
    tier: str,
    prompt_override: str | None = None,
) -> discord.Message | None:
    """Edit the interaction to prompt for a clip, wait for the upload message,
    and return it. Returns None on timeout.

    The caller is responsible for calling record_action (which downloads the
    clip bytes) BEFORE deleting the message — deleting first breaks the CDN URL.

    Parameters
    ----------
    bot:
        The bot instance used for wait_for.
    interaction:
        The original interaction to edit with prompts.
    tier:
        The tier string already chosen by the player (used in the prompt).
    prompt_override:
        If provided, use this text instead of the default prompt. Useful when
        re-prompting after a failed upload so the message isn't replaced.
    """
    prompt = prompt_override or (
        f"📎 Tier **{tier}** locked in.\n"
        f"Now send your clip (mp4/mov/webm/mkv) in this channel. "
        f"You have {CLIP_UPLOAD_TIMEOUT}s."
    )

    # Use edit_message for the initial response; edit_original_response for retries.
    if not interaction.response.is_done():
        await interaction.response.edit_message(content=prompt, view=None)
    else:
        await interaction.edit_original_response(content=prompt)

    def check(m: discord.Message) -> bool:
        return (
            m.author.id == interaction.user.id
            and m.channel.id == interaction.channel_id
            and len(m.attachments) > 0
        )

    try:
        msg: discord.Message = await bot.wait_for(
            "message", check=check, timeout=CLIP_UPLOAD_TIMEOUT
        )
    except asyncio.TimeoutError:
        await interaction.edit_original_response(
            content="⏰ Time's up — no clip received. Use the command again to retry."
        )
        return None

    return msg


# ---------------------------------------------------------------------------
# Post-turn result helper
# ---------------------------------------------------------------------------

async def post_turn_result(
    channel: discord.abc.Messageable,
    match_state,
    turn_summary: dict,
    generate_turn_embed_fn: Callable[..., Awaitable[discord.Embed]],
) -> None:
    """Post cached clips and the turn embed to the channel.

    Parameters
    ----------
    channel:
        The channel (or thread) to post into.
    match_state:
        The current MatchState or BossState.
    turn_summary:
        The dict returned by end_turn().
    generate_turn_embed_fn:
        The generate_turn_embed coroutine from combat_service.
    """
    acting_id = turn_summary["acting_player_id"]
    cached: list[dict] = match_state.video_cache.pop(acting_id, [])
    total = len(cached)

    for i, clip in enumerate(cached, start=1):
        await channel.send(
            content=f"📹 **Clip {i}/{total}** (<@{acting_id}>)",
            file=discord.File(io.BytesIO(clip["bytes"]), filename=clip["filename"]),
        )

    embed = await generate_turn_embed_fn(match_state, turn_summary)
    await channel.send(
        content=f"⚔️ **<@{acting_id}>** ended their turn.",
        embed=embed,
    )


# ---------------------------------------------------------------------------
# Shared TierSelectView
# ---------------------------------------------------------------------------

class TierSelectView(discord.ui.View):
    """Tier selection view shared by both PvP battle and boss battle.

    Parameters
    ----------
    action_type:
        "attack" | "defense" | "custom"
    bot:
        The bot instance, used for wait_for inside collect_clip.
    state_getter:
        A callable that takes an Interaction and returns the active
        match/boss state, or None. Used to look up the right state after
        the clip arrives.
    not_found_msg:
        Error message shown if state_getter returns None.
    """

    def __init__(
        self,
        action_type: str,
        bot: commands.Bot,
        state_getter: Callable[[int], object | None],
        not_found_msg: str = "This is not an active match channel.",
    ) -> None:
        super().__init__(timeout=60)
        self.action_type = action_type
        self.bot = bot
        self.state_getter = state_getter
        self.not_found_msg = not_found_msg

    async def _handle_tier(self, interaction: discord.Interaction, tier: str) -> None:
        self.stop()

        if settings.DEBUG:
            print(
                f"[DEBUG TierSelectView] channel_id={interaction.channel_id}  "
                f"user={interaction.user.id}  action={self.action_type}  tier={tier}"
            )

        from services.match_manager_service import match_manager as _mm
        state = self.state_getter(interaction)
        if not state:
            if settings.DEBUG:
                print(f"[DEBUG TierSelectView] No match found! active_matches={list(_mm._active_matches.keys())}")
            await interaction.response.edit_message(content=self.not_found_msg, view=None)
            return

        # Loop so a bad codec prompts the user to re-upload without restarting.
        # On first iteration collect_clip uses response.edit_message; on retries
        # it falls through to edit_original_response since response.is_done().
        prompt: str | None = None
        while True:
            msg = await collect_clip(self.bot, interaction, tier, prompt_override=prompt)
            if msg is None:
                return  # timeout already reported to user inside collect_clip

            attachment = msg.attachments[0]

            # Download and register the action BEFORE deleting the message.
            # Discord CDN URLs become inaccessible once the source message is deleted.
            success, reply, _resolution = await record_action(
                match_state=state,
                player_id=interaction.user.id,
                action_type=self.action_type,
                tier=tier,
                attachment=attachment,
            )

            # Always delete the user's upload message regardless of outcome.
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

            if success:
                await interaction.edit_original_response(content=reply)
                return

            # Action was rejected — set error as the next prompt and loop.
            prompt = (
                f"{reply}\n\n"
                f"⬆️ Send your corrected clip in this channel to try again "
                f"(tier **{tier}** is still locked in). You have {CLIP_UPLOAD_TIMEOUT}s."
            )

    @discord.ui.button(label="Normal", style=discord.ButtonStyle.secondary)
    async def normal_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._handle_tier(interaction, "Normal")

    @discord.ui.button(label="Medium", style=discord.ButtonStyle.primary)
    async def medium_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._handle_tier(interaction, "Medium")

    @discord.ui.button(label="Absolute", style=discord.ButtonStyle.danger)
    async def absolute_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._handle_tier(interaction, "Absolute")

    @discord.ui.button(label="Over-Absolute", style=discord.ButtonStyle.success)
    async def over_absolute_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._handle_tier(interaction, "Over-Absolute")
