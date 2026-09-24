import asyncio

from database.database import init_database


async def main() -> None:
    await init_database(generate_schemas=True)
    print("Connected to PostgreSQL & schemas created!")


if __name__ == "__main__":
    asyncio.run(main())
