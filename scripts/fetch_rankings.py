#!/usr/bin/env python3
"""Fetch TW or US trading rankings after market close, rebuild HTML, deploy.

Called by launchd after each market closes:
  - TW: 17:30 Taiwan time (after TWSE data is available)
  - US: 04:30 + 06:00 Taiwan time (04:30 catches summer/EDT, 06:00 catches winter/EST)

The script skips silently if today's data already exists (idempotent).
For US, it also checks that NYSE has actually closed before fetching.
After a successful fetch, rebuilds HTML and deploys to Cloudflare Workers.
CLOUDFLARE_API_TOKEN is read from .env only (never committed to git).
"""
import argparse
import asyncio
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.utils import db
from src.news.tw_rankings import fetch_and_store as fetch_tw
from src.news.us_rankings import fetch_and_store as fetch_us

BASE = Path(__file__).resolve().parent.parent
UV   = str(Path(os.environ.get("HOME", "")) / ".local/bin/uv")


def _nyse_closed() -> bool:
    """True if NYSE regular session has ended today (handles EDT/EST automatically)."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    close  = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return now_et >= close


def _rebuild_and_deploy() -> None:
    print("  ▶ 重建 HTML …")
    subprocess.run([UV, "run", "scripts/generate_html.py"], cwd=BASE, check=True)

    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        print("  ▶ 部署至 Cloudflare …")
        subprocess.run(
            ["npx", "wrangler", "deploy"],
            cwd=BASE, check=True,
            env={**os.environ, "CLOUDFLARE_API_TOKEN": token},
        )
    else:
        print("  ⚠ CLOUDFLARE_API_TOKEN 未設定，跳過部署")


async def main(market: str, force: bool) -> None:
    today = date.today()

    conn = await db.connect()
    exists = await conn.fetchval(
        "SELECT 1 FROM trading_rankings WHERE rank_date=$1 AND market=$2 LIMIT 1",
        today, market,
    )
    await conn.close()

    if exists and not force:
        print(f"  ⏭  {market} ranking 今日已存在（{today}）— 跳過")
        return

    if market == "US":
        if not _nyse_closed():
            now_et = datetime.now(ZoneInfo("America/New_York"))
            print(f"  ⏭  NYSE 尚未收盤（現在 ET: {now_et.strftime('%H:%M')}）— 跳過")
            return
        print(f"  ▶ 抓取 US rankings ({today}) …")
        n = await fetch_us(today)
        print(f"  ✅ 儲存 {n} 筆 US ranking")
    else:
        print(f"  ▶ 抓取 TW rankings ({today}) …")
        n = await fetch_tw(today)
        print(f"  ✅ 儲存 {n} 筆 TW ranking")

    _rebuild_and_deploy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["TW", "US"], required=True)
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even if today's data already exists")
    args = parser.parse_args()
    asyncio.run(main(args.market, args.force))
