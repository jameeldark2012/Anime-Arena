from __future__ import annotations

import asyncio

from scripts.db import db_tables_creation


def test_db_tables_creation_calls_init_database(monkeypatch):
    calls = []

    async def fake_init_database(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(db_tables_creation, "init_database", fake_init_database)

    asyncio.run(db_tables_creation.main())

    assert calls == [{"generate_schemas": True}]
