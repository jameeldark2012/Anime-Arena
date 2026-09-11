from __future__ import annotations

import discord
from discord.ext import commands

from core.config import settings

intents = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    if settings.GUILD_ID:
        guild = discord.Object(id=settings.GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


