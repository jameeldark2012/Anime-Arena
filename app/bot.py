from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord.ext import commands

from core.config import settings

intents = discord.Intents.default()
intents.message_content = True  # Required for wait_for("message") to receive attachment uploads
intents.messages = True          # Required to receive guild message events at all
bot = commands.Bot(command_prefix="!", intents=intents)
logger = logging.getLogger(__name__)


async def load_extensions() -> None:
    """Dynamically load all cogs from the app/cogs directory."""
    cogs_path = Path(__file__).parent / "cogs"
    for file in cogs_path.glob("*.py"):
        if file.name.startswith("_"):
            continue
        extension = f"app.cogs.{file.stem}"
        await bot.load_extension(extension)
        logger.info("Loaded extension: %s", extension)


@bot.event
async def on_ready() -> None:
    await load_extensions()

    if settings.GUILD_ID:
        guild = discord.Object(id=settings.GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
