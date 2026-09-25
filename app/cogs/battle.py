from __future__ import annotations

import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from services.match.match_manager_service import match_manager
from boss.boss_manager import boss_manager
from core.config import settings
from core.debug import debug_event
from services.combat.combat_service import (
    record_action,
    end_turn,
    generate_turn_embed,
    get_active_player,
    has_pending_attack,
)
from app.cogs.utils import (
    auto_delete,
    ref_ping,
    collect_clip,
    post_turn_result,
    TierSelectView,
    CLIP_UPLOAD_TIMEOUT,
)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class BattleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _delegate_boss_command(
        self, interaction: discord.Interaction, handler_name: str
    ) -> bool:
        """Route normal battle commands to their boss equivalents in boss threads."""
        boss_fight = boss_manager.get_fight_for_interaction(interaction)
        if boss_fight is None:
            player_fight = boss_manager.get_fight_for_player(interaction.user.id)
            if player_fight is None:
                return False
            boss_fight = player_fight

        boss_cog = self.bot.get_cog("BossBattleCog")
        handler = getattr(boss_cog, handler_name, None) if boss_cog else None
        if handler is None:
            await interaction.response.send_message(
                "Boss battle commands are temporarily unavailable.", ephemeral=True
            )
            return True

        await handler(interaction)
        return True

    def _validate(
        self, interaction: discord.Interaction
    ) -> tuple[object | None, str | None]:
        """Return (match_state, error). If error is set, abort."""
        debug_event(
            "_validate",
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
            active_matches=list(match_manager._active_matches.keys()),
        )
        match_state = match_manager.get_match_for_interaction(interaction)
        if not match_state:
            debug_event("_validate_fail", channel_id=interaction.channel_id, reason="no_match")
            return None, f"This command can only be used inside an active match channel. (ch={interaction.channel_id})"
        if interaction.user.id not in (match_state.player1_id, match_state.player2_id):
            debug_event(
                "_validate_fail",
                user_id=interaction.user.id,
                p1=match_state.player1_id,
                p2=match_state.player2_id,
                reason="user_not_in_match",
            )
            return None, "You are not a participant in this match."
        debug_event("_validate_ok", user_id=interaction.user.id)
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

        view = TierSelectView(
            action_type=action_type,
            bot=self.bot,
            state_getter=match_manager.get_match_for_interaction,
            not_found_msg="This is not an active match channel.",
        )
        await interaction.response.send_message(
            f"Select your **{action_type}** tier:", view=view, ephemeral=True
        )

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name="attack", description="Declare your attack and upload your clip")
    async def attack(self, interaction: discord.Interaction) -> None:
        if await self._delegate_boss_command(interaction, "submit_attack"):
            return
        await self._send_tier_view(interaction, "attack")

    @app_commands.command(
        name="defend",
        description="Declare a defense and upload your clip. Can be used multiple times.",
    )
    async def defend(self, interaction: discord.Interaction) -> None:
        if await self._delegate_boss_command(interaction, "submit_defense"):
            return
        await self._send_tier_view(interaction, "defense")

    @app_commands.command(
        name="custom",
        description="Submit a custom action clip (special ability, etc). No combat effect.",
    )
    async def custom(self, interaction: discord.Interaction) -> None:
        if await self._delegate_boss_command(interaction, "submit_custom"):
            return
        match_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        turn_err = self._guard_active_player(interaction, match_state)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel_id
                and len(m.attachments) > 0
            )

        prompt = f"📎 Send your custom action clip (mp4/mov/webm/mkv). You have {CLIP_UPLOAD_TIMEOUT}s."
        await interaction.response.send_message(prompt, ephemeral=True)

        # Loop so a bad codec prompts the user to re-upload without restarting.
        while True:
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

            if success:
                await interaction.edit_original_response(content=reply)
                return

            # Rejected — prompt to re-upload.
            await interaction.edit_original_response(
                content=(
                    f"{reply}\n\n"
                    f"⬆️ Send your corrected clip to try again. You have {CLIP_UPLOAD_TIMEOUT}s."
                )
            )

    @app_commands.command(
        name="end_turn",
        description="Lock in your actions and pass the turn to your opponent.",
    )
    async def end_turn_cmd(self, interaction: discord.Interaction) -> None:
        if await self._delegate_boss_command(interaction, "finish_player_turn"):
            return
        await interaction.response.defer(thinking=True, ephemeral=False)

        match_state = match_manager.get_match_for_interaction(interaction)
        if not match_state:
            debug_event(
                "end_turn_cmd_fail",
                channel_id=interaction.channel_id,
                active_matches=list(match_manager._active_matches.keys()),
            )
            await interaction.followup.send(f"This is not an active match channel. (ch={interaction.channel_id})", ephemeral=True)
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

        await interaction.followup.send("✅ Turn locked in.", ephemeral=True)

        await post_turn_result(interaction.channel, match_state, turn_summary, generate_turn_embed)

        # ── Match over ────────────────────────────────────────────────────────
        if turn_summary["winner_id"]:
            match_manager.remove_match(match_state.match_id)
            await interaction.channel.send(
                f"🏆 {ref_ping()} **MATCH OVER!** Winner: <@{turn_summary['winner_id']}>."
            )
        else:
            next_player_id = match_state.current_player_id
            next_msg = await interaction.channel.send(
                f"▶️ **Turn {match_state.current_turn}** — <@{next_player_id}>'s move!"
            )
            auto_delete(next_msg, delay=60)

    @app_commands.command(name="object", description="Raise an objection and ping a referee")
    async def object_match(self, interaction: discord.Interaction) -> None:
        match_state = match_manager.get_match_for_interaction(interaction)
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
        await interaction.response.send_message(
            f"🚨 **OBJECTION BY <@{interaction.user.id}>!** 🚨\n"
            f"⏸️ **Match is now PAUSED.** No actions can be submitted until a referee resolves this.\n"
            f"{ref_ping()} Please review this match immediately."
        )

    @app_commands.command(name="surrender", description="Forfeit the match and give your opponent the win")
    async def surrender(self, interaction: discord.Interaction) -> None:
        if await self._delegate_boss_command(interaction, "forfeit"):
            return
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

        await interaction.response.send_message(
            f"🏳️ <@{loser_id}> has **surrendered**!\n"
            f"🏆 <@{winner_id}> wins the match by forfeit!\n"
            f"{ref_ping()}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BattleCog(bot))
