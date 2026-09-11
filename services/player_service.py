from database.models.character import Character


async def get_current_character(user_id: int) -> Character | None:
    """Return the one character currently reserved by a player, if any."""
    return await Character.filter(claimed_by_id=user_id).select_related("anime").first()
