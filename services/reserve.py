from rapidfuzz import fuzz, process
from tortoise.transactions import in_transaction

from database.models.anime import Anime
from database.models.character import Character
from database.models.player import Player
from services.character_service import get_or_fetch_characters


async def change_player_reservation(
    user_id: int, target_character_id: int
) -> tuple[bool, str | None]:
    """Atomically replace a player's current character with an unclaimed target."""
    async with in_transaction():
        # Locking the player serializes simultaneous reserve attempts from the
        # same Discord account. The target lock prevents two players claiming it.
        await Player.get_or_create(user_id=user_id)
        player = await Player.filter(user_id=user_id).select_for_update().get()
        target = await Character.filter(
            character_id=target_character_id
        ).select_for_update().get()

        if target.claimed_by_id == player.user_id:
            return False, f"**{target.character_name}** is already your reserved character."
        if target.claimed_by_id is not None:
            return False, f"**{target.character_name}** is already reserved!"

        previous = await Character.filter(claimed_by_id=player.user_id).select_for_update().first()
        if previous:
            previous.claimed_by_id = None
            await previous.save(update_fields=["claimed_by_id"])

        target.claimed_by_id = player.user_id
        await target.save(update_fields=["claimed_by_id"])

    return True, previous.character_name if previous else None


async def reserve_character(
    user_id: int,
    anime_id: int,
    character_input: str,
) -> tuple[bool, str]:
    """Handles fuzzy matching, claim checks, and player reservation updates."""
    anime = await Anime.get_or_none(anime_id=anime_id)
    if not anime:
        return False, "Anime not found."

    # Always cache/link the selected anime before validating the typed name or
    # attempting a reservation. A failed reservation must not cause a re-scrape.
    characters = await get_or_fetch_characters(anime_id)
    if not characters:
        return False, f"No characters found for *{anime.anime_name}*."

    # 2. Fuzzy match user query against character names
    char_map = {c.character_name: c for c in characters}
    match = process.extractOne(
        character_input,
        list(char_map.keys()),
        scorer=fuzz.WRatio,
        score_cutoff=60,
    )

    if not match:
        return False, f"Could not find character '{character_input}' in *{anime.anime_name}*."

    target_char = char_map[match[0]]

    # The ownership check and swap happen in one transaction so stale command
    # input cannot overwrite somebody else's reservation.
    changed, detail = await change_player_reservation(user_id, target_char.character_id)
    if not changed:
        return False, detail or "That reservation could not be changed."

    if detail:
        return (
            True,
            f"Changed your reservation from **{detail}** to **{target_char.character_name}** "
            f"from *{anime.anime_name}*!"
        )

    return True, f"Successfully reserved **{target_char.character_name}** from *{anime.anime_name}*!"
