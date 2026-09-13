from __future__ import annotations

import io
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from boss.boss_manager import boss_manager
from boss.boss_ai import run_boss_turn
from boss.boss_config import BOSSES, BOSS_PLAYER_ID
from services.combat_service import (
    record_action,
    end_turn,
    generate_turn_embed,
    get_active_player,
    has_pending_attack,
)
from core.config import settings

logger = logging.getLogger(__name__)

CLIP_UPLOAD_TIMEOUT = 120
TEMP_MSG_TTL = 30


def _auto_delete(msg: discord.Message, delay: float = TEMP_MSG_TTL) -> None:
    async def _delete() -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
    asyncio.create_task(_delete())


# ---------------------------------------------------------------------------
# Tier selection view — identical pattern to battle.py but uses boss_manager
# ---------------------------------------------------------------------------

class BossTierSelectView(discord.ui.View):
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

        boss_state = boss_manager.get_fight(interaction.channel_id)
        if not boss_state:
            await interaction.edit_original_response(content="This is not an active boss fight channel.")
            return

        success, reply, _resolution = await record_action(
            match_state=boss_state,
            player_id=interaction.user.id,
            action_type=self.action_type,
            tier=tier,
            attachment=attachment,
        )

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
        """Run the boss AI turn and post the result embed. Handles match-over."""
        turn_summary = await run_boss_turn(boss_state, channel)
        if turn_summary is None:
            return

        # Post the status embed.
        embed = await generate_turn_embed(boss_state, turn_summary)
        await channel.send(
            content=f"👹 **{boss_state.boss_config.display_name}** ended their turn.",
            embed=embed,
        )

        # Check for match over.
        if turn_summary["winner_id"]:
            await _handle_match_over(boss_state, channel, turn_summary["winner_id"])
        else:
            next_msg = await channel.send(
                f"▶️ **Turn {boss_state.current_turn}** — <@{boss_state.player1_id}>'s move!"
            )
            _auto_delete(next_msg, delay=60)

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

    @app_commands.command(name="boss_attack", description="Declare an attack in your boss fight.")
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

        view = BossTierSelectView("attack", self.bot)
        await interaction.response.send_message(
            "Select your **attack** tier:", view=view, ephemeral=True
        )

    @app_commands.command(name="boss_defend", description="Declare a defense in your boss fight.")
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

        view = BossTierSelectView("defense", self.bot)
        await interaction.response.send_message(
            "Select your **defense** tier:", view=view, ephemeral=True
        )

    @app_commands.command(
        name="boss_custom",
        description="Submit a custom/RP clip in your boss fight. No combat effect.",
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

        await interaction.edit_original_response(content=reply if success else f"❌ {reply}")

    @app_commands.command(
        name="boss_end_turn",
        description="Lock in your actions and let the boss respond.",
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

        # Post the player's clips.
        acting_id = turn_summary["acting_player_id"]
        cached: list[dict] = boss_state.video_cache.pop(acting_id, [])
        total = len(cached)
        for i, clip in enumerate(cached, start=1):
            await interaction.channel.send(
                content=f"📹 **Clip {i}/{total}** (<@{acting_id}>)",
                file=discord.File(io.BytesIO(clip["bytes"]), filename=clip["filename"]),
            )

        # Post the player's turn embed.
        embed = await generate_turn_embed(boss_state, turn_summary)
        await interaction.channel.send(
            content=f"⚔️ **<@{acting_id}>** ended their turn.",
            embed=embed,
        )

        # ── Check if the boss is already dead (player's attack just killed it) ─
        if turn_summary["winner_id"]:
            await _handle_match_over(boss_state, interaction.channel, turn_summary["winner_id"])
            return

        # ── Boss takes its turn automatically ─────────────────────────────────
        await interaction.channel.send("⚙️ **The boss is responding…**")
        await self._run_boss_turn_and_post(boss_state, interaction.channel)

    @app_commands.command(
        name="boss_surrender",
        description="Forfeit your boss fight.",
    )
    async def boss_surrender(self, interaction: discord.Interaction) -> None:
        await self.forfeit(interaction)

    async def forfeit(self, interaction: discord.Interaction) -> None:
        boss_state, err = self._validate(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        boss_manager.remove_fight(boss_state.match_id)
        await interaction.response.send_message(
            f"🏳️ <@{interaction.user.id}> has **surrendered** the boss fight!\n"
            f"**{boss_state.boss_config.display_name}** remains undefeated…"
        )


# ---------------------------------------------------------------------------
# Shared match-over handler
# ---------------------------------------------------------------------------

async def _handle_match_over(boss_state, channel: discord.abc.Messageable, winner_id: int) -> None:
    boss_manager.remove_fight(boss_state.match_id)
    config = boss_state.boss_config

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

    ref_ping = f"<@&{settings.REFEREE_ROLE_ID}>" if settings.REFEREE_ROLE_ID else ""
    await channel.send(f"🏁 **BOSS FIGHT OVER!**\n{result}\n{ref_ping}".strip())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BossBattleCog(bot))
