#!/usr/bin/env python3
"""Podcast refinement backfill — auto-detects and re-processes incomplete episodes.

Runs daily at 07:00 (after podcast-crawl at 06:00, before daily-briefing at 07:30).
Also safe to run manually at any time.

Logic:
  - Finds podcast episodes where content_tags is NULL or empty within last LOOKBACK_DAYS
  - Calls Gemini 2.5 Flash with CALL_DELAY between requests to avoid 429
  - On 429/error: skips episode (will retry next run)
  - Rebuilds HTML if any episodes were updated
"""
import asyncio
from src.utils import db
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from src.utils.refine import refine_content, embed_text

PODCAST_SOURCES = [
    "podcast_gooaye",
    "podcast_macromicro",
    "podcast_chives_grad",
    "podcast_stock_barrel",
    "podcast_zhaohua",
    "podcast_statementdog",
]
MAX_PER_SOURCE = 5    # episodes per source per run (paid tier, no strict quota)
LOOKBACK_DAYS  = 30   # only process episodes from the last N days
CALL_DELAY     = 2    # seconds between calls (courtesy buffer, paid tier)


async def find_incomplete(conn) -> list[dict]:
    """Return podcast episodes that lack structured refinement (no valid tags)."""
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    result = []
    for src in PODCAST_SOURCES:
        rows = await conn.fetch("""
            SELECT id, source, title, published_at, content
            FROM (
              SELECT DISTINCT ON (title)
                     id, source, title, published_at, content, content_tags
              FROM articles
              WHERE source = $1
                AND status = 'active'
                AND content IS NOT NULL
                AND published_at >= $2
              ORDER BY title, published_at DESC NULLS LAST
            ) deduped
            WHERE content_tags IS NULL OR content_tags = '{}'
            ORDER BY published_at DESC NULLS LAST
            LIMIT $3
        """, src, cutoff, MAX_PER_SOURCE)
        result.extend(dict(r) for r in rows)
    return result


async def main() -> int:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("⚠ GOOGLE_API_KEY not set — skipping podcast backfill")
        return 0

    conn = await db.connect()
    episodes = await find_incomplete(conn)

    if not episodes:
        print("✅ All podcast episodes up to date — nothing to backfill")
        await conn.close()
        return 0

    print(f"Found {len(episodes)} episode(s) needing refinement:")
    updated = 0

    for i, ep in enumerate(episodes):
        if i > 0:
            time.sleep(CALL_DELAY)

        dt  = str(ep["published_at"])[:10]
        ttl = ep["title"][:60]
        print(f"  [{ep['id']}] {dt} {ttl}")

        result = refine_content(ep["content"], ep["title"], is_podcast=True)
        if result is None:
            print(f"    ⚠ Gemini unavailable (429?) — will retry next run")
            continue

        refined, tags = result
        if not refined:
            print(f"    → NONE (no investment content)")
            await conn.execute(
                "UPDATE articles SET refined_content=NULL, content_tags='{}', updated_at=NOW() WHERE id=$1",
                ep["id"],
            )
            continue

        embedding = embed_text(refined)
        if embedding is not None:
            vec_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
            await conn.execute("""
                UPDATE articles
                SET refined_content=$1, content_tags=$2,
                    embedding=CAST($3 AS vector), updated_at=NOW()
                WHERE id=$4
            """, refined, tags, vec_str, ep["id"])
        else:
            await conn.execute("""
                UPDATE articles
                SET refined_content=$1, content_tags=$2, updated_at=NOW()
                WHERE id=$3
            """, refined, tags, ep["id"])

        print(f"    ✓ tags={tags} {len(refined)} chars")
        updated += 1

    await conn.close()
    print(f"\n{'✅' if updated else '⚠'} Refined {updated}/{len(episodes)} episode(s)")

    if updated > 0:
        print("Rebuilding HTML…")
        proc = subprocess.run(
            ["uv", "run", "python", "scripts/generate_html.py"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True,
        )
        print((proc.stdout or proc.stderr).strip())

    return updated


if __name__ == "__main__":
    asyncio.run(main())
