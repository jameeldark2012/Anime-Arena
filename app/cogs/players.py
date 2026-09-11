from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services import get_all_players_with_characters


class PlayersCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="players", description="Show all players and their reserved characters")
    async def players(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        entries = await get_all_players_with_characters()
        if not entries:
            await interaction.followup.send("No players have registered yet.")
            return

        lines: list[str] = []
        for player, character in entries:
            user = interaction.guild.get_member(player.user_id) if interaction.guild else None
            if user is None:
                try:
                    user = await self.bot.fetch_user(player.user_id)
                except discord.NotFound:
                    pass
            name = user.display_name if user else str(player.user_id)

            if character:
                anime_name = character.anime.anime_name if character.anime else "Unknown Anime"
                lines.append(f"**{name}** — {character.character_name} (*{anime_name}*)")
            else:
                lines.append(f"**{name}** — *No character reserved*")

        await interaction.followup.send("\n".join(lines))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayersCog(bot))
