from database.models.anime import Anime


async def search_anime_by_title(query: str, limit: int = 25) -> list[Anime]:
    """Queries anime titles matching the user input for autocomplete."""
    return await Anime.filter(anime_name__icontains=query).limit(limit)
