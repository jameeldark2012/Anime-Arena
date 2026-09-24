from database.models.character import Character
from database.models.player import Player


async def get_current_character(user_id: int) -> Character | None:
    """Returns the character claimed by the given player, or None if unclaimed."""
    return await Character.get_or_none(claimed_by_id=user_id).select_related("anime")


async def get_all_players_with_characters() -> list[tuple[Player, Character | None]]:
    """Returns all players paired with their claimed character (or None if unclaimed)."""
    players = await Player.all()
    if not players:
        return []

    characters = await Character.filter(
        claimed_by_id__in=[p.user_id for p in players]
    ).select_related("anime")

    char_by_player: dict[int, Character] = {c.claimed_by_id: c for c in characters}

    return [(player, char_by_player.get(player.user_id)) for player in players]
