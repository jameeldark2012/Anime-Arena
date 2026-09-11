import ast
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / "config.env")
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing from config.env")

engine = create_engine(DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1).replace("ssl=true", "sslmode=require"))
chunk_size = 5000


def prepare_anime_data() -> pd.DataFrame:
    df = pd.read_csv(PROJECT_ROOT / "data" / "anime_cleaned.csv")
    df["genres"] = df["genres"].apply(
        lambda value: json.dumps(ast.literal_eval(value)) if pd.notna(value) else json.dumps([])
    )
    return df


def prepare_character_data() -> pd.DataFrame:
    df = pd.read_csv(PROJECT_ROOT / "data" / "characters_cleaned.csv")
    df["anime_id"] = df["anime_id"].replace(-1, None)
    df["claimed_by"] = df["claimed_by"].replace(-1, None)
    df = df.rename(columns={"claimed_by": "claimed_by_id", "main_picture": "image_url"})

    keep_columns = ["character_id", "character_name", "anime_id", "image_url", "claimed_by_id"]
    return df[[column for column in keep_columns if column in df.columns]]


def bulk_insert_dataframe(table_name: str, dataframe: pd.DataFrame) -> None:
    total_rows = len(dataframe)
    with tqdm(total=total_rows, desc=f"Inserting {table_name}") as progress:
        for start in range(0, total_rows, chunk_size):
            chunk = dataframe.iloc[start:start + chunk_size]
            chunk.to_sql(table_name, engine, if_exists="append", index=False, method="multi")
            progress.update(len(chunk))


def main() -> None:
    bulk_insert_dataframe("anime", prepare_anime_data())
    bulk_insert_dataframe("character", prepare_character_data())
    print("Done! All data imported successfully.")


if __name__ == "__main__":
    main()
