#!/usr/bin/env python3
import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv()

async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    sources = [
        'investanchors', 'macromicro', 'vocus_chivesking',
        'statementdog', 'pressplay',
        'podcast_gooaye', 'podcast_macromicro',
        'podcast_chives_grad', 'podcast_stock_barrel',
    ]
    for src in sources:
        row = await conn.fetchrow(
            """SELECT source, title, published_at, content, url
               FROM articles WHERE source=$1
               ORDER BY published_at DESC NULLS LAST LIMIT 1""", src
        )
        if row:
            print(f"\n{'='*60}")
            print(f"SOURCE: {row['source']}")
            print(f"TITLE:  {row['title']}")
            print(f"DATE:   {str(row['published_at'])[:10] if row['published_at'] else 'N/A'}")
            print(f"URL:    {row['url'][:80]}")
            print(f"CONTENT ({len(row['content'])} chars):")
            print(row['content'][:800])
        else:
            print(f"\n[{src}] — 無資料")
    await conn.close()

asyncio.run(main())
