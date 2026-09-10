import os
import ast
import json
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from tqdm import tqdm

load_dotenv("dbconfig.env")
DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1).replace("ssl=true", "sslmode=require")

engine = create_engine(DATABASE_URL)
chunk_size = 5000

# 1. Anime Import
df_anime = pd.read_csv("data/anime_cleaned.csv")
# Serialize lists to JSON strings for database compatibility
df_anime['genres'] = df_anime['genres'].apply(lambda x: json.dumps(ast.literal_eval(x)) if pd.notna(x) else json.dumps([]))

total_anime = len(df_anime)
with tqdm(total=total_anime, desc="Inserting Anime") as pbar:
    for i in range(0, total_anime, chunk_size):
        chunk = df_anime.iloc[i:i+chunk_size]
        chunk.to_sql("anime", engine, if_exists="append", index=False, method="multi")
        pbar.update(len(chunk))

# 2. Character Import
df_chars = pd.read_csv("data/characters_cleaned.csv")
df_chars['anime_id'] = df_chars['anime_id'].replace(-1, None)
df_chars['claimed_by'] = df_chars['claimed_by'].replace(-1, None)
df_chars.rename(columns={'claimed_by': 'claimed_by_id', 'main_picture': 'image_url'}, inplace=True)

keep_cols = ['character_id', 'character_name', 'anime_id', 'image_url', 'claimed_by_id']
df_chars = df_chars[[c for c in keep_cols if c in df_chars.columns]]

total_chars = len(df_chars)
with tqdm(total=total_chars, desc="Inserting Characters") as pbar:
    for i in range(0, total_chars, chunk_size):
        chunk = df_chars.iloc[i:i+chunk_size]
        chunk.to_sql("character", engine, if_exists="append", index=False, method="multi")
        pbar.update(len(chunk))

print("Done! All data imported successfully.")