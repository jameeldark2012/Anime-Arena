from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Test command")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pong!")

    @app_commands.command(name="help", description="Show the available slash commands")
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Anime Arena Commands",
            description="Use / to open the command list in Discord, or use this quick cheat sheet.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="General",
            value="/ping\n/help",
            inline=False,
        )
        embed.add_field(
            name="Player setup",
            value="/reserve\n/my_character\n/players\n/player",
            inline=False,
        )
        embed.add_field(
            name="Matchmaking",
            value="/challenge",
            inline=False,
        )
        embed.add_field(
            name="Battle",
            value="/attack\n/defend\n/custom\n/end_turn\n/object\n/surrender",
            inline=False,
        )
        embed.add_field(
            name="Boss fight",
            value="/boss_fight",
            inline=False,
        )
        embed.add_field(
            name="Referee",
            value="/ref_view\n/ref_set_hp\n/ref_rollback\n/ref_declare_winner\n/ref_boss_wins\n/ref_resume",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GeneralCog(bot))
