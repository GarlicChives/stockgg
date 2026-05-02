#!/usr/bin/env python3
"""Backfill refined_content + embeddings for all existing articles in DB.

Run once after applying migration 002. Safe to re-run — skips articles
that already have refined_content set.

Usage:
    uv run scripts/backfill_refined.py [--limit N] [--source SOURCE]
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
from dotenv import load_dotenv

from src.utils.refine import refine_and_store

load_dotenv()

BATCH = 10  # process N articles before printing progress


async def main(limit: int = 0, source: str = ""):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    where = "refined_content IS NULL"
    params: list = []
    if source:
        where += f" AND source=${len(params)+1}"
        params.append(source)

    count_q = f"SELECT COUNT(*) FROM articles WHERE {where}"
    total = await conn.fetchval(count_q, *params)
    print(f"Articles needing refinement: {total}")

    if limit:
        fetch_q = f"SELECT id, title, content FROM articles WHERE {where} ORDER BY published_at DESC LIMIT {limit}"
    else:
        fetch_q = f"SELECT id, title, content FROM articles WHERE {where} ORDER BY published_at DESC"

    rows = await conn.fetch(fetch_q, *params)
    print(f"Processing {len(rows)} articles…\n")

    done = 0
    skipped = 0
    for row in rows:
        title = row["title"] or ""
        content = row["content"] or ""
        if not content:
            skipped += 1
            continue

        updated = await refine_and_store(conn, row["id"], title, content)
        if updated:
            done += 1
        else:
            skipped += 1

        if done % BATCH == 0 and done > 0:
            print(f"  {done}/{len(rows)} done ({skipped} skipped)")

    print(f"\nDone. {done} articles refined, {skipped} skipped.")
    await conn.close()


if __name__ == "__main__":
    limit = 0
    source = ""
    args = sys.argv[1:]
    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1])
    if "--source" in args:
        idx = args.index("--source")
        source = args[idx + 1]

    asyncio.run(main(limit=limit, source=source))
