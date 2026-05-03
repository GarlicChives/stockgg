#!/usr/bin/env python3
"""Full daily briefing pipeline.

1. Fetch market data (indices, VIX, 10Y, Fear & Greed)
2. Fetch US + TW trading value rankings (TWSE + TPEX merged)
3. Generate M3 analysis report via Gemini
4. Generate cross-source market notes via Gemini
5. Cleanup articles/news older than 180 days

Usage:
    uv run scripts/daily_briefing.py              # full pipeline
    uv run scripts/daily_briefing.py --skip-fetch # use existing DB data, only regen reports
"""
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from src.news.market_data import fetch_and_store as fetch_indicators
from src.news.us_rankings import fetch_and_store as fetch_us
from src.news.tw_rankings import fetch_and_store as fetch_tw
from src.analysis.daily_report import generate_report
from src.analysis.market_notes import generate_market_notes
import asyncpg


async def cleanup_old_data(conn) -> None:
    """Delete stale data: articles 180 days, podcasts 30 days, news 180 days."""
    result = await conn.execute(
        "DELETE FROM articles WHERE created_at < NOW() - INTERVAL '180 days' AND source NOT LIKE 'podcast_%'"
    )
    art_del = int((result.split()[-1]) if result else 0)

    result = await conn.execute(
        "DELETE FROM articles WHERE created_at < NOW() - INTERVAL '30 days' AND source LIKE 'podcast_%'"
    )
    pod_del = int((result.split()[-1]) if result else 0)

    result = await conn.execute(
        "DELETE FROM news_items WHERE created_at < NOW() - INTERVAL '180 days'"
    )
    news_del = int((result.split()[-1]) if result else 0)

    if art_del or pod_del or news_del:
        print(f"  Cleaned up: {art_del} articles, {pod_del} podcasts, {news_del} news items")


async def main():
    skip_fetch = "--skip-fetch" in sys.argv
    today = date.today()

    if not skip_fetch:
        print("── Step 1: Market Indicators ──")
        await fetch_indicators(today)
        print()
        print("── Step 2: US Trading Rankings ──")
        await fetch_us(today)
        print()
        print("── Step 3: TW Trading Rankings (TWSE + TPEX) ──")
        await fetch_tw()
        print()

    print("── Step 4: M3 Analysis Report ──")
    report = await generate_report(today)
    print()
    print("=" * 60)
    print(report)
    print("=" * 60)
    print()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        print("── Step 5: Cross-Source Market Notes ──")
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await generate_market_notes(conn, today, api_key)
        finally:
            await conn.close()
        print()
    else:
        print("── Step 5: Market Notes skipped (no GOOGLE_API_KEY) ──")
        print()

    if api_key:
        print("── Step 5.5: Theme Dictionary (Incremental Append) ──")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "build_theme_dictionary.py")],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True,
        )
        print((proc.stdout or proc.stderr).strip())
        print()
    else:
        print("── Step 5.5: Theme Dictionary skipped (no GOOGLE_API_KEY) ──")
        print()

    print("── Step 6: Cleanup Old Data (>180 days) ──")
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await cleanup_old_data(conn)
    finally:
        await conn.close()
    print()

    print("── Step 7: Rebuild HTML ──")
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "generate_html.py")],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True,
    )
    print((proc.stdout or proc.stderr).strip())
    print()

    print("── Step 8: API 使用成本報告 ──")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "api_cost_check.py")],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True,
    )
    print((proc.stdout or proc.stderr).strip())
    print()

    print("── Done ──")


if __name__ == "__main__":
    asyncio.run(main())
