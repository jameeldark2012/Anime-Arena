from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services import get_all_players_with_characters, get_current_character


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
            # Skip internal bot/boss accounts (negative or zero user IDs).
            if player.user_id <= 0:
                continue

            user = interaction.guild.get_member(player.user_id) if interaction.guild else None
            if user is None:
                try:
                    user = await self.bot.fetch_user(player.user_id)
                except (discord.NotFound, discord.HTTPException):
                    pass
            name = user.display_name if user else str(player.user_id)

            if character:
                anime_name = character.anime.anime_name if character.anime else "Unknown Anime"
                lines.append(f"**{name}** — {character.character_name} (*{anime_name}*)")
            else:
                lines.append(f"**{name}** — *No character reserved*")

        if not lines:
            await interaction.followup.send("No players have registered yet.")
            return

        await interaction.followup.send("\n".join(lines))
    @app_commands.command(name="player", description="Show the reserved character of a specified player (or yourself)")
    @app_commands.describe(target="Discord ID or @mention of the player (optional) – defaults to yourself")
    async def player(self, interaction: discord.Interaction, target: str | None = None) -> None:
        """Return the current character reserved by the specified player.

        The *target* argument can be omitted to query the command issuer.
        If a value is provided it is interpreted as either a raw numeric
        Discord ID or a mention of the form ``<@123456789>``.  The command
        fetches the member from the guild if present; otherwise it falls back
        to ``bot.fetch_user`` so that characters can be looked up for users
        who are not in the guild.
        """
        await interaction.response.defer()

        # Resolve user id to query.
        if target is None:
            user_id = interaction.user.id
        else:
            # Strip common mention syntax.
            if target.startswith("<@"):
                # Remove any exclamation marks used for nicknames.
                cleaned = target.strip("<>@!")
                try:
                    user_id = int(cleaned)
                except ValueError:
                    await interaction.followup.send(
                        "Invalid target format. Provide a numeric Discord ID or a mention."
                    )
                    return
            else:
                # Assume a raw numeric string.
                try:
                    user_id = int(target)
                except ValueError:
                    await interaction.followup.send(
                        "Invalid target format. Provide a numeric Discord ID or a mention."
                    )
                    return

        # Resolve display name – try guild first, then fetch.
        member = interaction.guild.get_member(user_id) if interaction.guild else None
        if member is None:
            try:
                member = await self.bot.fetch_user(user_id)
            except Exception:
                # If the user cannot be fetched, fall back to the ID string.
                member = None
        # Prefer display_name for guild members; fallback to name for Users.
        if member:
            name = getattr(member, "display_name", getattr(member, "name", str(user_id)))
        else:
            name = str(user_id)

        # Look up the character.
        character = await get_current_character(user_id)
        if not character:
            # No reserved character.
            if user_id == interaction.user.id:
                await interaction.followup.send("You do not currently have a reserved character.")
            else:
                await interaction.followup.send(f"**{name}** does not have a reserved character.")
            return

        anime_name = character.anime.anime_name if character.anime else "an unknown anime"
        if user_id == interaction.user.id:
            await interaction.followup.send(
                f"Your current character is **{character.character_name}** from *{anime_name}*."
            )
        else:
            await interaction.followup.send(
                f"**{name}**'s current character is **{character.character_name}** from *{anime_name}*."
            )



    # Removed duplicate /player command that accepted a discord.User
    # This block was causing a registration conflict and was no longer needed.


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayersCog(bot))
