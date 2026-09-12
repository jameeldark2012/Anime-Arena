from __future__ import annotations

import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from services.match_manager_service import match_manager

# How long the challenged player has to respond before the challenge expires.
CHALLENGE_TIMEOUT = 60


class ChallengeView(discord.ui.View):
    """Shown to the challenged player. Only they can interact with it."""

    def __init__(self, challenger_id: int, target_id: int) -> None:
        super().__init__(timeout=CHALLENGE_TIMEOUT)
        self.challenger_id = challenger_id
        self.target_id = target_id
        self.accepted: bool | None = None  # None = timed out

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_id:
            await interaction.response.send_message(
                "This challenge isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.accepted = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.accepted = False
        self.stop()
        await interaction.response.defer()


class MatchmakingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="challenge", description="Challenge another player to a match")
    @app_commands.describe(target="The player you want to challenge")
    async def challenge(self, interaction: discord.Interaction, target: discord.Member) -> None:
        await interaction.response.defer(thinking=True)

        if target.id == interaction.user.id:
            await interaction.followup.send("You cannot challenge yourself!")
            return

        if target.bot:
            await interaction.followup.send("You cannot challenge a bot!")
            return

        # Check pair conflict before sending the challenge UI.
        pair = frozenset({interaction.user.id, target.id})
        if pair in match_manager._active_pairs:
            await interaction.followup.send(
                "You two already have an active match against each other!"
            )
            return

        # Send the challenge request publicly so everyone can see it.
        view = ChallengeView(
            challenger_id=interaction.user.id,
            target_id=target.id,
        )
        challenge_msg = await interaction.followup.send(
            f"⚔️ <@{interaction.user.id}> is challenging <@{target.id}> to a match!\n"
            f"<@{target.id}> — do you accept? You have {CHALLENGE_TIMEOUT}s to respond.",
            view=view,
        )

        # Wait for the target to respond (or time out).
        await view.wait()

        if view.accepted is None:
            # Timed out.
            await challenge_msg.edit(
                content=(
                    f"⌛ <@{target.id}> didn't respond in time. "
                    f"Challenge from <@{interaction.user.id}> has expired."
                ),
                view=None,
            )
            return

        if not view.accepted:
            await challenge_msg.edit(
                content=(
                    f"❌ <@{target.id}> declined the challenge from <@{interaction.user.id}>."
                ),
                view=None,
            )
            return

        # Accepted — create the match.
        await challenge_msg.edit(
            content=(
                f"✅ <@{target.id}> accepted the challenge from <@{interaction.user.id}>! "
                f"Creating match…"
            ),
            view=None,
        )

        match_state, error = await match_manager.create_match_post(
            guild=interaction.guild,
            player1_id=interaction.user.id,
            player2_id=target.id,
        )

        if error or not match_state:
            await challenge_msg.edit(
                content=f"❌ Could not start match: {error}",
                view=None,
            )
            return

        await challenge_msg.edit(
            content=(
                f"✅ Match created! <@{interaction.user.id}> vs <@{target.id}>\n"
                f"Head to the matches channel. <@{match_state.current_player_id}> goes first!"
            ),
            view=None,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MatchmakingCog(bot))
