from __future__ import annotations

import asyncio

from app.bot import bot
from core.config import settings
from database.database import init_database


async def main() -> None:
    await init_database()
    await bot.start(settings.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
