from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from boss.boss_manager import boss_manager
from boss.boss_ai import run_boss_turn, run_boss_intro
from boss.boss_config import BOSSES, BOSS_PLAYER_ID
from services.combat_service import (
    record_action,
    end_turn,
    generate_turn_embed,
    get_active_player,
)
from app.cogs.utils import (
    auto_delete,
    ref_ping,
    collect_clip,
    post_turn_result,
    TierSelectView,
    CLIP_UPLOAD_TIMEOUT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class BossBattleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_fight(self, interaction: discord.Interaction):
        """Return the BossState for this channel, or None."""
        return boss_manager.get_fight(interaction.channel_id)

    def _validate(self, interaction: discord.Interaction) -> tuple[object | None, str | None]:
        boss_state = self._get_fight(interaction)
        if not boss_state:
            return None, "This command can only be used inside an active boss fight channel."
        if interaction.user.id != boss_state.player1_id:
            return None, "You are not the challenger in this boss fight."
        return boss_state, None

    def _guard_player_turn(self, interaction: discord.Interaction, boss_state) -> str | None:
        """Return an error string if it's not the human player's turn."""
        if boss_state.is_boss_turn:
            return "⚙️ Wait — the boss is taking its turn."
        if interaction.user.id != get_active_player(boss_state):
            return "It's not your turn."
        return None

    async def _run_boss_turn_and_post(
        self,
        boss_state,
        channel: discord.abc.Messageable,
    ) -> None:
        """Run the boss AI turn. Clips and embed are posted inside run_boss_turn."""
        turn_summary = await run_boss_turn(boss_state, channel)
        if turn_summary is None:
            return

        if turn_summary["winner_id"]:
            await _handle_match_over(boss_state, channel, turn_summary["winner_id"])
        else:
            next_msg = await channel.send(
                f"▶️ **Turn {boss_state.current_turn}** — <@{boss_state.player1_id}>'s move!"
            )
            auto_delete(next_msg, delay=60)

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="boss_fight",
        description="Start a 1v1 fight against a boss.",
    )
    @app_commands.describe(boss="Which boss to fight (e.g. zeke)")
    async def boss_fight(self, interaction: discord.Interaction, boss: str = "zeke") -> None:
        await interaction.response.defer(thinking=True)

        boss_slug = boss.lower().strip()
        if boss_slug not in BOSSES:
            available = ", ".join(BOSSES.keys())
            await interaction.followup.send(
                f"❌ Unknown boss `{boss_slug}`. Available: {available}.", ephemeral=True
            )
            return

        boss_state, error = await boss_manager.create_boss_fight(
            guild=interaction.guild,
            player_id=interaction.user.id,
            boss_slug=boss_slug,
        )

        if error or not boss_state:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return

        await interaction.followup.send(
            f"⚡ Boss fight started! Head to the match thread. "
            f"You go first — good luck against **{boss_state.boss_config.display_name}**!",
        )

        # Post the boss's intro clip (if the script defines one).
        thread = interaction.guild.get_channel(boss_state.match_id)
        if thread is None:
            try:
                thread = await interaction.guild.fetch_channel(boss_state.match_id)
            except Exception:
                thread = None
        if thread:
            await run_boss_intro(boss_state, thread)

    async def boss_attack(self, interaction: discord.Interaction) -> None:
        await self.submit_attack(interaction)

    async def submit_attack(self, interaction: discord.Interaction) -> None:
        boss_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        turn_err = self._guard_player_turn(interaction, boss_state)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return

        if any(a["action_type"] == "attack" for a in boss_state.current_turn_actions):
            await interaction.response.send_message(
                "You already declared an attack this turn.", ephemeral=True
            )
            return

        view = TierSelectView(
            action_type="attack",
            bot=self.bot,
            state_getter=lambda interaction: boss_manager.get_fight(interaction.channel_id),
            not_found_msg="This is not an active boss fight channel.",
        )
        await interaction.response.send_message(
            "Select your **attack** tier:", view=view, ephemeral=True
        )

    async def boss_defend(self, interaction: discord.Interaction) -> None:
        await self.submit_defense(interaction)

    async def submit_defense(self, interaction: discord.Interaction) -> None:
        boss_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        turn_err = self._guard_player_turn(interaction, boss_state)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return

        view = TierSelectView(
            action_type="defense",
            bot=self.bot,
            state_getter=lambda interaction: boss_manager.get_fight(interaction.channel_id),
            not_found_msg="This is not an active boss fight channel.",
        )
        await interaction.response.send_message(
            "Select your **defense** tier:", view=view, ephemeral=True
        )

    async def boss_custom(self, interaction: discord.Interaction) -> None:
        await self.submit_custom(interaction)

    async def submit_custom(self, interaction: discord.Interaction) -> None:
        boss_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        turn_err = self._guard_player_turn(interaction, boss_state)
        if turn_err:
            await interaction.response.send_message(turn_err, ephemeral=True)
            return

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel_id
                and len(m.attachments) > 0
            )

        await interaction.response.send_message(
            f"📎 Send your custom action clip (mp4/mov/webm/mkv). You have {CLIP_UPLOAD_TIMEOUT}s.",
            ephemeral=True,
        )

        # Loop so a bad codec prompts the user to re-upload without restarting.
        while True:
            try:
                msg: discord.Message = await self.bot.wait_for(
                    "message", check=check, timeout=CLIP_UPLOAD_TIMEOUT
                )
            except asyncio.TimeoutError:
                await interaction.edit_original_response(content="⏰ Time's up — no clip received.")
                return

            attachment = msg.attachments[0]
            success, reply, _ = await record_action(
                match_state=boss_state,
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

    async def boss_end_turn(self, interaction: discord.Interaction) -> None:
        await self.finish_player_turn(interaction)

    async def finish_player_turn(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=False)

        boss_state = boss_manager.get_fight(interaction.channel_id)
        if not boss_state:
            await interaction.followup.send("This is not an active boss fight channel.", ephemeral=True)
            return

        if interaction.user.id != boss_state.player1_id:
            await interaction.followup.send("You are not the challenger in this boss fight.", ephemeral=True)
            return

        turn_err = self._guard_player_turn(interaction, boss_state)
        if turn_err:
            await interaction.followup.send(turn_err, ephemeral=True)
            return

        # ── End the player's turn ─────────────────────────────────────────────
        success, message, turn_summary = await end_turn(boss_state, interaction.user.id)
        if not success:
            await interaction.followup.send(message, ephemeral=True)
            return

        await interaction.followup.send("✅ Turn locked in.", ephemeral=True)

        await post_turn_result(interaction.channel, boss_state, turn_summary, generate_turn_embed)

        # ── Check if the boss is already dead (player's attack just killed it) ─
        if turn_summary["winner_id"]:
            await _handle_match_over(boss_state, interaction.channel, turn_summary["winner_id"])
            return

        # ── Boss takes its turn automatically ─────────────────────────────────
        await interaction.channel.send("⚙️ **The boss is responding…**")
        await self._run_boss_turn_and_post(boss_state, interaction.channel)

    async def boss_surrender(self, interaction: discord.Interaction) -> None:
        await self.forfeit(interaction)

    async def forfeit(self, interaction: discord.Interaction) -> None:
        boss_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        await interaction.response.send_message(
            f"🏳️ <@{interaction.user.id}> has **surrendered** the boss fight!\n"
            f"**{boss_state.boss_config.display_name}** remains undefeated…"
        )
        # Fire the victory clip, then tear down the fight.
        await _handle_match_over(boss_state, interaction.channel, BOSS_PLAYER_ID)


# ---------------------------------------------------------------------------
# Shared match-over handler
# ---------------------------------------------------------------------------

async def _fire_boss_victory_or_defeat_clip(
    boss_state,
    channel: discord.abc.Messageable,
    winner_id: int,
) -> None:
    """Post Zeke's on_victory or on_defeat clip before the match is torn down."""
    from boss.boss_ai import _read_clip, _post_clip

    script = boss_state.script
    config = boss_state.boss_config

    if winner_id == BOSS_PLAYER_ID:
        path = script.on_victory(boss_state)
        if path:
            await _post_clip(
                channel,
                f"💀 **{config.display_name}** stands victorious.",
                await _read_clip(path),
            )
    else:
        path = script.on_defeat(boss_state)
        if path:
            await _post_clip(
                channel,
                f"🏆 **{config.display_name}** has been defeated!",
                await _read_clip(path),
            )


async def _handle_match_over(boss_state, channel: discord.abc.Messageable, winner_id: int) -> None:
    config = boss_state.boss_config

    # Fire the victory/defeat clip before tearing down the fight.
    await _fire_boss_victory_or_defeat_clip(boss_state, channel, winner_id)

    boss_manager.remove_fight(boss_state.match_id)

    if winner_id == BOSS_PLAYER_ID:
        result = (
            f"💀 **{config.display_name}** has defeated <@{boss_state.player1_id}>!\n"
            f"Better luck next time."
        )
    else:
        result = (
            f"🏆 <@{boss_state.player1_id}> has **defeated {config.display_name}**! "
            f"Incredible!"
        )

    ping = ref_ping()
    await channel.send(f"🏁 **BOSS FIGHT OVER!**\n{result}\n{ping}".strip())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BossBattleCog(bot))
