from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.match_manager_service import match_manager


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

        match_state, error = await match_manager.create_match_post(
            guild=interaction.guild,
            player1_id=interaction.user.id,
            player2_id=target.id,
        )

        if error or not match_state:
            await interaction.followup.send(f"Could not start match: {error}")
            return

        await interaction.followup.send(
            f"Match successfully created! Head over to the matches channel to fight.\n"
            f"<@{match_state.current_player_id}> goes first!"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MatchmakingCog(bot))
