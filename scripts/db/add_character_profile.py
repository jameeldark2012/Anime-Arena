"""Migration: add profile column to character table.

Safe to run multiple times — uses IF NOT EXISTS.
Does not modify any existing rows or columns.

Usage:
    python -m scripts.db.add_character_profile
"""
from __future__ import annotations

import asyncio

from database.database import init_database
from tortoise import Tortoise


async def main() -> None:
    await init_database()
    conn = Tortoise.get_connection("default")
    await conn.execute_query(
        "ALTER TABLE character ADD COLUMN IF NOT EXISTS profile TEXT;"
    )
    print("[OK] Column 'profile' added to 'character' table (or already existed).")
    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
