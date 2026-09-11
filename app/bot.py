from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.config import settings
from services import (
    get_cached_characters,
    get_current_character,
    prefetch_characters,
    reserve_character,
    search_anime_by_title,
)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
logger = logging.getLogger(__name__)


async def anime_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Fetches matching anime titles via service layer for command dropdown."""
    animes = await search_anime_by_title(current)
    return [
        app_commands.Choice(name=anime.anime_name[:100], value=anime.anime_id)
        for anime in animes
    ]


async def character_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggest cached characters and asynchronously warm an empty anime cache."""
    anime_id = getattr(interaction.namespace, "anime", None)
    if not isinstance(anime_id, int):
        return []

    characters = await get_cached_characters(anime_id)
    if not characters:
        prefetch_characters(anime_id)
        return []

    current = current.casefold()
    return [
        app_commands.Choice(name=character.character_name[:100], value=character.character_name[:100])
        for character in characters
        if current in character.character_name.casefold()
    ][:25]


@bot.event
async def on_ready() -> None:
    if settings.GUILD_ID:
        guild = discord.Object(id=settings.GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.tree.command(name="ping", description="Test command")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Pong!")


@bot.tree.command(name="my_character", description="Show your currently reserved character")
async def my_character(interaction: discord.Interaction) -> None:
    character = await get_current_character(interaction.user.id)
    if not character:
        await interaction.response.send_message("You do not currently have a reserved character.")
        return

    anime_name = character.anime.anime_name if character.anime else "an unknown anime"
    await interaction.response.send_message(
        f"Your current character is **{character.character_name}** from *{anime_name}*."
    )


@bot.tree.command(name="reserve", description="Reserve a character from an anime")
@app_commands.autocomplete(anime=anime_autocomplete, character_name=character_autocomplete)
async def reserve(
    interaction: discord.Interaction,
    anime: int,
    character_name: str,
) -> None:
    await interaction.response.defer()

    try:
        _success, message = await reserve_character(
            user_id=interaction.user.id,
            anime_id=anime,
            character_input=character_name,
        )
    except Exception:
        # A deferred Discord interaction needs an explicit follow-up even when
        # a dependency/database call fails, otherwise it stays "Executing".
        logger.exception("Reserve command failed for anime %s", anime)
        message = "I couldn't reserve that character right now. Please try again in a moment."

    await interaction.followup.send(message)
