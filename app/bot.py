from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord.ext import commands

from core.config import settings

intents = discord.Intents.default()
intents.message_content = True  # Required for wait_for("message") to receive attachment uploads
intents.messages = True          # Required to receive guild message events at all

logger = logging.getLogger(__name__)


class ArenaBot(commands.Bot):
    async def setup_hook(self) -> None:
        """Runs once on startup before on_ready. Load extensions and sync commands here."""
        cogs_path = Path(__file__).parent / "cogs"
        for file in cogs_path.glob("*.py"):
            if file.name.startswith("_") or file.stem == "utils":
                continue
            extension = f"app.cogs.{file.stem}"
            await self.load_extension(extension)
            logger.info("Loaded extension: %s", extension)

        if settings.GUILD_ID:
            guild = discord.Object(id=settings.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (ID: {self.user.id})")


bot = ArenaBot(command_prefix="!", intents=intents)
