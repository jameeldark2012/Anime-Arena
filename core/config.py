from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / "config.env"

load_dotenv(ENV_FILE)


class Settings:
    """Centralized application settings."""

    DISCORD_BOT_TOKEN: str
    DATABASE_URL: str
    GUILD_ID: int

    def __init__(self) -> None:
        self.DISCORD_BOT_TOKEN = self._required("DISCORD_BOT_TOKEN")
        self.DATABASE_URL = self._required("DATABASE_URL")
        self.GUILD_ID = self._int("GUILD_ID", 0)

    @staticmethod
    def _required(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return value

    @staticmethod
    def _int(name: str, default: int) -> int:
        value = os.getenv(name, "")
        if not value:
            return default
        return int(value)


settings = Settings()
