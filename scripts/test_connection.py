import os
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / "config.env"

print("Loaded:", load_dotenv(env_path))
print("URL:", os.getenv("DATABASE_URL"))
