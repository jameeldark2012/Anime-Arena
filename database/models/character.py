from tortoise import fields
from tortoise.models import Model


class Character(Model):
    character_id = fields.IntField(pk=True, generated=False)
    anime = fields.ForeignKeyField("models.Anime", related_name="anime_characters", null=True)
    character_name = fields.TextField()
    image_url = fields.TextField(null=True)
    claimed_by = fields.OneToOneField("models.Player", related_name="claimed_character", null=True)

    class Meta:
        table = "character"
