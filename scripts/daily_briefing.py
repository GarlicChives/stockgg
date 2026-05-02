#!/usr/bin/env python3
"""Full daily briefing pipeline.

1. Fetch market data (indices, VIX, 10Y, Fear & Greed)
2. Fetch US + TW trading value rankings
3. Generate M3 analysis report via Gemini (requires GOOGLE_API_KEY)
4. Print the report

Usage:
    uv run scripts/daily_briefing.py          # full pipeline
    uv run scripts/daily_briefing.py --skip-fetch   # use existing DB data, only generate report
"""
import asyncio
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
        print("── Step 3: TW Trading Rankings ──")
        await fetch_tw()
        print()

    print("── Step 4: M3 Analysis Report ──")
    report = await generate_report(today)
    print()
    print("=" * 60)
    print(report)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
