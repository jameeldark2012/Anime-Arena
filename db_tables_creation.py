import asyncio
import os
from dotenv import load_dotenv
from tortoise import Tortoise, fields
from tortoise.models import Model
from tortoise.models import Model

# Load your custom env file
load_dotenv("dbconfig.env")
DATABASE_URL = os.getenv("DATABASE_URL")


class Anime(Model):

  anime_id = fields.IntField(pk=True , generated = False)
  anime_name = fields.TextField()
  score = fields.FloatField(null = True)
  genres = fields.JSONField(null = True)
  main_picture = fields.TextField(null = True)
  url = fields.TextField(null = True)
  trailer_url = fields.TextField(null = True)
  title_english = fields.TextField(null = True)
  title_japanese = fields.TextField(null = True)


class Player(Model):

  user_id = fields.BigIntField(pk=True , generated = False)  # Discord ID as primary key

class Character(Model):
  
  character_id = fields.IntField(pk=True , generated = False)
  anime = fields.ForeignKeyField("models.Anime", related_name="anime_characters", null= True)
  character_name = fields.TextField()
  image_url = fields.TextField(null=True)
  claimed_by = fields.OneToOneField(
      "models.Player", related_name="claimed_character", null=True
  )


async def main():
  await Tortoise.init(db_url=DATABASE_URL, modules={"models": [__name__]})
  await Tortoise.generate_schemas()
  print("Connected to PostgreSQL & schemas created!")


if __name__ == "__main__":
  asyncio.run(main())