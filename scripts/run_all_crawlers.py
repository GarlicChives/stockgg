#!/usr/bin/env python3
"""Run all 5 column crawlers incrementally (for scheduled launchd runs).

Each crawler connects to Chrome via CDP (port 9222).
Usage:
    uv run scripts/run_all_crawlers.py --incremental
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

import src.crawlers.macromicro as macromicro
import src.crawlers.vocus as vocus
import src.crawlers.statementdog as statementdog
import src.crawlers.investanchors as investanchors
import src.crawlers.pressplay as pressplay

CRAWLERS = [
    ("MacroMicro",    macromicro.crawl),
    ("Vocus 韭菜王",  vocus.crawl),
    ("StatementDog",  statementdog.crawl),
    ("InvestAnchors", investanchors.crawl),
    ("PressPlay",     pressplay.crawl),
]


async def main(incremental: bool = False):
    total = 0
    for name, crawl_fn in CRAWLERS:
        print(f"\n{'='*50}")
        print(f"  {name}")
        print(f"{'='*50}")
        try:
            n = await crawl_fn(incremental=incremental)
            total += n or 0
        except Exception as e:
            print(f"  ERROR: {e}")
    print(f"\n\nAll crawlers done. {total} new articles saved.")


if __name__ == "__main__":
    asyncio.run(main(incremental="--incremental" in sys.argv))
