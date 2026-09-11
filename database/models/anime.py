from tortoise import fields
from tortoise.models import Model


class Anime(Model):
    anime_id = fields.IntField(pk=True, generated=False)
    anime_name = fields.TextField()
    score = fields.FloatField(null=True)
    genres = fields.JSONField(null=True)
    main_picture = fields.TextField(null=True)
    url = fields.TextField(null=True)
    trailer_url = fields.TextField(null=True)
    title_english = fields.TextField(null=True)
    title_japanese = fields.TextField(null=True)

    class Meta:
        table = "anime"
