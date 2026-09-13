"""Seed script — creates the boss Player row and links the boss Character.

Run once after setting up the database:
    python -m scripts.seed_boss

What it does:
1. Creates a Player row with user_id = BOSS_PLAYER_ID (-1).
   This is the shared sentinel identity for ALL bosses.
2. For each boss defined in boss/boss_config.py, finds the matching
   Character row by character_id and sets claimed_by = -1.

You must set the correct character_id in boss/boss_config.py BEFORE
running this script. To find the right character_id, query the DB:
    SELECT character_id, character_name FROM character WHERE character_name ILIKE '%zeke%';

Re-running this script is safe — it uses get_or_create / update so it
won't duplicate rows or crash if the boss Player already exists.
"""

import asyncio

from database.database import init_database
from database.models.player import Player
from database.models.character import Character
from boss.boss_config import BOSS_PLAYER_ID, BOSSES
from tortoise import Tortoise


async def main() -> None:
    await init_database()

    # ── Step 1: Ensure the boss Player row exists ─────────────────────────────
    boss_player, created = await Player.get_or_create(user_id=BOSS_PLAYER_ID)
    if created:
        print(f"✅ Created boss Player row (user_id={BOSS_PLAYER_ID}).")
    else:
        print(f"ℹ️  Boss Player row already exists (user_id={BOSS_PLAYER_ID}).")

    # ── Step 2: Link each boss config to its Character row ────────────────────
    for slug, config in BOSSES.items():
        if config.character_id == 0:
            print(
                f"⚠️  Boss '{slug}' has character_id=0 (placeholder). "
                f"Update boss_config.py with the real character_id, then re-run."
            )
            continue

        char = await Character.get_or_none(character_id=config.character_id)
        if char is None:
            print(
                f"❌ Boss '{slug}': no Character found with character_id={config.character_id}. "
                f"Check boss_config.py."
            )
            continue

        if char.claimed_by_id == BOSS_PLAYER_ID:
            print(
                f"ℹ️  Boss '{slug}': Character '{char.character_name}' "
                f"already claimed by boss player."
            )
        else:
            char.claimed_by_id = BOSS_PLAYER_ID
            await char.save(update_fields=["claimed_by_id"])
            print(
                f"✅ Boss '{slug}': linked Character '{char.character_name}' "
                f"(id={config.character_id}) to boss player."
            )

    await Tortoise.close_connections()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
