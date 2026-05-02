#!/usr/bin/env python3
"""Daily market data runner — fetches indices, US rankings, TW rankings.

Usage:
    uv run scripts/fetch_market_data.py          # today / last trading day
    uv run scripts/fetch_market_data.py --date 2025-05-01   # specific date
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.news.market_data import fetch_and_store as fetch_indicators
from src.news.us_rankings import fetch_and_store as fetch_us
from src.news.tw_rankings import fetch_and_store as fetch_tw


async def main(target_date: date | None = None):
    print("=" * 55)
    print(f"  Daily Market Data Fetch — {target_date or 'today'}")
    print("=" * 55)

    await fetch_indicators(target_date)
    print()
    await fetch_us(target_date)
    print()
    await fetch_tw(target_date)
    print()
    print("All done.")


if __name__ == "__main__":
    target = None
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        target = date.fromisoformat(sys.argv[idx + 1])
    asyncio.run(main(target))
