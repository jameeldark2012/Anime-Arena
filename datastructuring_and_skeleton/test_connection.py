import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / "config.env"

# Prints True if the file is found and loaded
print("Loaded:", load_dotenv(env_path))

# Prints your database URL string
print("URL:", os.getenv("DATABASE_URL"))