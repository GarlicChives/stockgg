#!/usr/bin/env python3
"""Run all migrations against the Supabase PostgreSQL database."""
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def main():
    url = os.environ["DATABASE_URL"]
    migrations_dir = Path(__file__).parent.parent / "migrations"

    print(f"Connecting to database...")
    conn = await asyncpg.connect(url)
    print("Connected.")

    for sql_file in sorted(migrations_dir.glob("*.sql")):
        print(f"Running {sql_file.name}...")
        sql = sql_file.read_text()
        await conn.execute(sql)
        print(f"  Done.")

    await conn.close()
    print("All migrations applied successfully.")


if __name__ == "__main__":
    asyncio.run(main())
