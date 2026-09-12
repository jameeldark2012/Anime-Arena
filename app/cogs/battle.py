from __future__ import annotations

import io
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from services.match_manager_service import match_manager
from services.combat_service import (
    record_action,
    end_turn,
    generate_turn_embed,
    get_active_player,
    has_pending_attack,
)
from core.config import settings

# Timeout (seconds) to wait for the user to upload their clip.
CLIP_UPLOAD_TIMEOUT = 120
# How long (seconds) temporary public messages linger before auto-deletion.
TEMP_MSG_TTL = 30


def _auto_delete(msg: discord.Message, delay: float = TEMP_MSG_TTL) -> None:
    """Schedule a message for deletion after `delay` seconds. Fire-and-forget."""
    async def _delete() -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
    asyncio.create_task(_delete())


# ---------------------------------------------------------------------------
# Tier selection view (shared by /attack and /defend)
# ---------------------------------------------------------------------------

class TierSelectView(discord.ui.View):
    def __init__(self, action_type: str, bot: commands.Bot) -> None:
        super().__init__(timeout=60)
        self.action_type = action_type
        self.bot = bot

    async def _handle_tier(self, interaction: discord.Interaction, tier: str) -> None:
        self.stop()

        await interaction.response.edit_message(
            content=(
                f"📎 Tier **{tier}** locked in.\n"
                f"Now send your clip (mp4/mov/webm/mkv) in this channel. "
                f"You have {CLIP_UPLOAD_TIMEOUT}s."
            ),
            view=None,
        )

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel_id
                and len(m.attachments) > 0
            )

        try:
            msg: discord.Message = await self.bot.wait_for(
                "message", check=check, timeout=CLIP_UPLOAD_TIMEOUT
            )
        except asyncio.TimeoutError:
            await interaction.edit_original_response(
                content="⏰ Time's up — no clip received. Use the command again to retry."
            )
            return

        attachment = msg.attachments[0]

        match_state = match_manager.get_match(interaction.channel_id)
        if not match_state:
            await interaction.edit_original_response(content="This is not an active match channel.")
            return

        # Download and register the action BEFORE deleting the message.
        # Discord CDN URLs become inaccessible once the source message is deleted.
        success, reply, _resolution = await record_action(
            match_state=match_state,
            player_id=interaction.user.id,
            action_type=self.action_type,
            tier=tier,
            attachment=attachment,
        )

        # Now safe to delete — bytes are already cached in match_state.
        try:
            await msg.delete()
        except discord.HTTPException:
            pass

        await interaction.edit_original_response(content=reply if success else f"❌ {reply}")

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


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class BattleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _validate(
        self, interaction: discord.Interaction
    ) -> tuple[object | None, str | None]:
        """Return (match_state, error). If error is set, abort."""
        match_state = match_manager.get_match(interaction.channel_id)
        if not match_state:
            return None, "This command can only be used inside an active match channel."
        if interaction.user.id not in (match_state.player1_id, match_state.player2_id):
            return None, "You are not a participant in this match."
        return match_state, None

    def _guard_active_player(
        self, interaction: discord.Interaction, match_state
    ) -> str | None:
        """Return an error string if it's not this player's turn, else None."""
        if interaction.user.id != get_active_player(match_state):
            other_id = (
                match_state.player2_id
                if interaction.user.id == match_state.player1_id
                else match_state.player1_id
            )
            return f"It's not your turn — waiting for <@{other_id}> to finish their turn."
        return None

    async def _send_tier_view(
        self, interaction: discord.Interaction, action_type: str
    ) -> None:
        match_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        turn_err = self._guard_active_player(interaction, match_state)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return

        # At most one attack per turn.
        if action_type == "attack" and any(
            a["action_type"] == "attack" for a in match_state.current_turn_actions
        ):
            await interaction.response.send_message(
                "You already declared an attack this turn.",
                ephemeral=True,
            )
            return

        # If there's a pending incoming attack and this player hasn't acted yet,
        # remind them they should defend first (but don't force it — the engine
        # will resolve it correctly regardless).
        if (
            action_type != "defense"
            and has_pending_attack(match_state)
            and len(match_state.current_turn_actions) == 0
        ):
            # Let the command proceed but warn them — they may intentionally eat the damage.
            pass  # warning is shown via the resolution message after they submit

        view = TierSelectView(action_type, self.bot)
        await interaction.response.send_message(
            f"Select your **{action_type}** tier:", view=view, ephemeral=True
        )

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name="attack", description="Declare your attack and upload your clip")
    async def attack(self, interaction: discord.Interaction) -> None:
        await self._send_tier_view(interaction, "attack")

    @app_commands.command(
        name="defend",
        description="Declare a defense and upload your clip. Can be used multiple times.",
    )
    async def defend(self, interaction: discord.Interaction) -> None:
        await self._send_tier_view(interaction, "defense")

    @app_commands.command(
        name="custom",
        description="Submit a custom action clip (special ability, etc). No combat effect.",
    )
    async def custom(self, interaction: discord.Interaction) -> None:
        match_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        turn_err = self._guard_active_player(interaction, match_state)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return

        await interaction.response.send_message(
            f"📎 Send your custom action clip (mp4/mov/webm/mkv). You have {CLIP_UPLOAD_TIMEOUT}s.",
            ephemeral=True,
        )

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel_id
                and len(m.attachments) > 0
            )

        try:
            msg: discord.Message = await self.bot.wait_for(
                "message", check=check, timeout=CLIP_UPLOAD_TIMEOUT
            )
        except asyncio.TimeoutError:
            await interaction.edit_original_response(
                content="⏰ Time's up — no clip received."
            )
            return

        attachment = msg.attachments[0]

        # Download first, then delete.
        success, reply, _ = await record_action(
            match_state=match_state,
            player_id=interaction.user.id,
            action_type="custom",
            tier=None,
            attachment=attachment,
        )

        try:
            await msg.delete()
        except discord.HTTPException:
            pass

        await interaction.edit_original_response(content=reply if success else f"❌ {reply}")

    @app_commands.command(
        name="end_turn",
        description="Lock in your actions and pass the turn to your opponent.",
    )
    async def end_turn_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=False)

        match_state = match_manager.get_match(interaction.channel_id)
        if not match_state:
            await interaction.followup.send("This is not an active match channel.", ephemeral=True)
            return

        if interaction.user.id not in (match_state.player1_id, match_state.player2_id):
            await interaction.followup.send("You are not a participant in this match.", ephemeral=True)
            return

        turn_err = self._guard_active_player(interaction, match_state)
        if turn_err:
            await interaction.followup.send(turn_err, ephemeral=True)
            return

        success, message, turn_summary = await end_turn(match_state, interaction.user.id)

        if not success:
            await interaction.followup.send(message, ephemeral=True)
            return

        # ── Acknowledge the interaction silently, then post everything via
        #    channel.send so all messages share the same pipeline and order
        #    is guaranteed (clips first, status embed last).
        await interaction.followup.send("✅ Turn locked in.", ephemeral=True)

        acting_id = turn_summary["acting_player_id"]
        cached: list[dict] = match_state.video_cache.pop(acting_id, [])

        actions = turn_summary["actions"]
        action_line = (
            " → ".join(
                a["action_type"].upper() + (f" ({a['tier']})" if a.get("tier") else "")
                for a in actions
            )
            if actions else "PASSED"
        )

        # 1. Clips in submission order.
        total = len(cached)
        for i, clip in enumerate(cached, start=1):
            await interaction.channel.send(
                content=f"📹 **Clip {i}/{total} — {clip['label']}** (<@{acting_id}>)",
                file=discord.File(io.BytesIO(clip["bytes"]), filename=clip["filename"]),
            )

        # 2. Status embed last.
        embed = await generate_turn_embed(match_state, turn_summary)
        await interaction.channel.send(
            content=f"⚔️ **<@{acting_id}>** ended their turn: **{action_line}**",
            embed=embed,
        )

        # ── Match over ────────────────────────────────────────────────────────
        if turn_summary["winner_id"]:
            match_manager.remove_match(match_state.match_id)
            ref_ping = f"<@&{settings.REFEREE_ROLE_ID}>" if settings.REFEREE_ROLE_ID else "@here"
            await interaction.channel.send(
                f"🏆 {ref_ping} **MATCH OVER!** Winner: <@{turn_summary['winner_id']}>."
            )
        else:
            next_player_id = match_state.current_player_id
            pending_notice = ""
            if has_pending_attack(match_state):
                atk = match_state.pending_attack
                pending_notice = (
                    f"\n⚠️ <@{next_player_id}> — you have an incoming **{atk['tier']}** attack! "
                    f"Your **first action must be a defense** or you take full damage."
                )
            next_msg = await interaction.channel.send(
                f"▶️ **Turn {match_state.current_turn}** — <@{next_player_id}>'s move!{pending_notice}"
            )
            _auto_delete(next_msg, delay=60)

    @app_commands.command(name="object", description="Raise an objection and ping a referee")
    async def object_match(self, interaction: discord.Interaction) -> None:
        match_state = match_manager.get_match(interaction.channel_id)
        if not match_state:
            await interaction.response.send_message(
                "This is not an active match channel.", ephemeral=True
            )
            return

        if interaction.user.id not in (match_state.player1_id, match_state.player2_id):
            await interaction.response.send_message(
                "You are not a participant in this match.", ephemeral=True
            )
            return

        match_state.has_objection = True
        match_state.is_paused = True
        ref_ping = f"<@&{settings.REFEREE_ROLE_ID}>" if settings.REFEREE_ROLE_ID else "@here"
        await interaction.response.send_message(
            f"🚨 **OBJECTION BY <@{interaction.user.id}>!** 🚨\n"
            f"⏸️ **Match is now PAUSED.** No actions can be submitted until a referee resolves this.\n"
            f"{ref_ping} Please review this match immediately."
        )

    @app_commands.command(name="surrender", description="Forfeit the match and give your opponent the win")
    async def surrender(self, interaction: discord.Interaction) -> None:
        match_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        loser_id = interaction.user.id
        winner_id = (
            match_state.player2_id
            if loser_id == match_state.player1_id
            else match_state.player1_id
        )

        match_state.status = "finished"
        match_manager.remove_match(match_state.match_id)

        ref_ping = f"<@&{settings.REFEREE_ROLE_ID}>" if settings.REFEREE_ROLE_ID else "@here"
        await interaction.response.send_message(
            f"🏳️ <@{loser_id}> has **surrendered**!\n"
            f"🏆 <@{winner_id}> wins the match by forfeit!\n"
            f"{ref_ping}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BattleCog(bot))
