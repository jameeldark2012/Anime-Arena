from tortoise import fields
from tortoise.models import Model


class Player(Model):
    user_id = fields.BigIntField(pk=True, generated=False)

    class Meta:
        table = "player"
