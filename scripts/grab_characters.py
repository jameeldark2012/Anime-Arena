import re

import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz import process


def search_anime(user_query: str, threshold: int = 60):
    """Independent fuzzy search function for the local anime dataset."""
    df = pd.read_csv("data/anime.csv")
    titles = df["title"].dropna().tolist()

    best_match, score, _ = process.extractOne(user_query, titles)
    print(f"Search: '{user_query}' -> Matched '{best_match}' ({score}%)")

    if score < threshold:
        return None

    return df[df["title"] == best_match].iloc[0]


def get_characters(user_query: str):
    """Fetch characters for an anime by pulling the anime page and parsing character links."""
    match_row = search_anime(user_query)
    if match_row is None:
        return ["Anime not found"]

    url = f"{match_row['url'].rstrip('/')}/characters"
    soup = BeautifulSoup(
        requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text,
        "html.parser",
    )

    container = soup.find("div", class_="anime-character-container")
    if not container:
        return []

    characters = []
    seen_ids = set()

    for anchor in container.select("a[href*='/character/']"):
        name = anchor.text.strip()
        if not name:
            continue

        href = anchor.get("href", "")
        match = re.search(r"/character/(\d+)", href)
        char_id = match.group(1) if match else None

        if char_id and char_id not in seen_ids:
            seen_ids.add(char_id)
            characters.append({"id": char_id, "name": name})

    return characters


if __name__ == "__main__":
    print(get_characters("bakemonogatari"))
