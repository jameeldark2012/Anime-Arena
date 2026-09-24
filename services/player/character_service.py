from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import aiohttp
from bs4 import BeautifulSoup

from database.models.anime import Anime
from database.models.character import Character

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 12

_fetch_locks: dict[int, asyncio.Lock] = {}
_prefetch_tasks: dict[int, asyncio.Task[list[Character]]] = {}


@dataclass(frozen=True)
class CharacterData:
    character_id: int
    character_name: str
    image_url: str | None


def _fetch_lock(anime_id: int) -> asyncio.Lock:
    """Return the per-anime lock used to prevent duplicate API fetches."""
    return _fetch_locks.setdefault(anime_id, asyncio.Lock())


async def get_cached_characters(anime_id: int) -> list[Character]:
    """Return cached characters only; this never performs a network request."""
    return await Character.filter(anime_id=anime_id).order_by("character_name")


async def _fetch_character_data(anime: Anime) -> list[CharacterData]:
    """Scrape the selected anime's MAL character page without blocking the bot."""
    if not anime.url:
        logger.warning("Anime %s has no MAL URL to scrape", anime.anime_id)
        return []

    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
    url = f"{anime.url.rstrip('/')}/characters"

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            if response.status != 200:
                logger.warning(
                    "Character scraper for anime %s returned HTTP %s", anime.anime_id, response.status
                )
                return []
            html = await response.text()

    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", class_="anime-character-container")
    if not container:
        logger.warning("Character scraper found no character section for anime %s", anime.anime_id)
        return []

    characters: list[CharacterData] = []
    seen_ids: set[int] = set()
    for anchor in container.select("a[href*='/character/']"):
        name = anchor.get_text(strip=True)
        match = re.search(r"/character/(\d+)", anchor.get("href", ""))
        if not name or not match:
            continue
        character_id = int(match.group(1))
        if character_id in seen_ids:
            continue

        seen_ids.add(character_id)
        characters.append(CharacterData(character_id, name, None))

    return characters


async def fetch_and_cache_characters(anime: Anime) -> list[Character]:
    """Fetch missing characters for one anime and cache them without blocking the event loop."""
    try:
        fetched_characters = await _fetch_character_data(anime)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Unable to fetch characters for anime %s: %s", anime.anime_id, exc)
        return []

    if not fetched_characters:
        return []

    character_ids = [character.character_id for character in fetched_characters]
    existing = {
        character.character_id: character
        for character in await Character.filter(character_id__in=character_ids)
    }

    new_characters = [
        Character(
            character_id=character.character_id,
            character_name=character.character_name,
            image_url=character.image_url,
            anime=anime,
        )
        for character in fetched_characters
        if character.character_id not in existing
    ]
    if new_characters:
        await Character.bulk_create(new_characters)

    # The imported dataset contains unlinked character rows. Link matching rows
    # rather than attempting to insert a duplicate primary key.
    unlinked_ids = [
        character_id
        for character_id, character in existing.items()
        if character.anime_id is None
    ]
    if unlinked_ids:
        await Character.filter(character_id__in=unlinked_ids, anime_id__isnull=True).update(
            anime_id=anime.anime_id
        )

    return await get_cached_characters(anime.anime_id)


async def get_or_fetch_characters(anime_id: int) -> list[Character]:
    """Return cached characters, fetching them once when this anime has none."""
    characters = await get_cached_characters(anime_id)
    if characters:
        return characters

    async with _fetch_lock(anime_id):
        characters = await get_cached_characters(anime_id)
        if characters:
            return characters

        anime = await Anime.get_or_none(anime_id=anime_id)
        if not anime:
            return []
        return await fetch_and_cache_characters(anime)


def prefetch_characters(anime_id: int) -> None:
    """Start one non-blocking cache fill for autocomplete, if one is not already running."""
    task = _prefetch_tasks.get(anime_id)
    if task and not task.done():
        return

    task = asyncio.create_task(get_or_fetch_characters(anime_id))
    _prefetch_tasks[anime_id] = task

    def _clear_task(completed_task: asyncio.Task[list[Character]]) -> None:
        _prefetch_tasks.pop(anime_id, None)
        try:
            completed_task.result()
        except Exception:
            logger.exception("Background character prefetch failed for anime %s", anime_id)

    task.add_done_callback(_clear_task)
