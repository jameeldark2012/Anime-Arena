from __future__ import annotations

from tortoise import Tortoise

from core.config import settings


async def init_database(*, generate_schemas: bool = False) -> None:
    """Initialize Tortoise and optionally generate table schemas.

    Schema generation is intentionally optional so app startup does not
    recreate or reset existing data on every run.
    """
    await Tortoise.init(
        db_url=settings.DATABASE_URL,
        modules={"models": ["database.models"]},
    )

    if generate_schemas:
        await Tortoise.generate_schemas()
