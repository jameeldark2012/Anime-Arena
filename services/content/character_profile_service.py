"""Character profile service.

Provides read/write access to the `profile` field on the Character model.
Profiles are plain text (markdown-friendly) and store lore, appearance,
personality, powers, and combat rules for a character.

Used by:
- The AI player to understand both its own character and its opponent.
- Any future feature that needs character background (ref tools, display, etc.).
"""
from __future__ import annotations

from database.models.character import Character


async def get_profile(character_id: int) -> str | None:
    """Return the stored profile text for a character, or None if not set."""
    char = await Character.get_or_none(character_id=character_id)
    if char is None:
        return None
    return char.profile or None


async def set_profile(character_id: int, profile: str) -> bool:
    """Set or replace the profile text for a character.

    Returns True if the character was found and updated, False if not found.
    """
    char = await Character.get_or_none(character_id=character_id)
    if char is None:
        return False
    char.profile = profile.strip()
    await char.save(update_fields=["profile"])
    return True


async def get_profile_by_name(character_name: str) -> str | None:
    """Look up a profile by character name (case-insensitive exact match).

    Returns the profile text, or None if the character doesn't exist or has no profile.
    """
    char = await Character.get_or_none(character_name__iexact=character_name)
    if char is None:
        return None
    return char.profile or None
