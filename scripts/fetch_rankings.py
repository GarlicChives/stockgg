#!/usr/bin/env python3
"""Fetch TW or US trading rankings after market close, rebuild HTML, deploy.

Called by launchd after each market closes:
  - TW: 17:30 Taiwan time (after TWSE data is available)
  - US: 04:30 + 06:00 Taiwan time (04:30 catches summer/EDT, 06:00 catches winter/EST)

Retry logic:
  - If fetch returns 0 rows or raises an exception, retries up to MAX_RETRIES times,
    each RETRY_INTERVAL_S seconds apart (default: 3 retries × 1 hour).
  - After exhausting retries with 0 rows, the day is treated as a holiday/non-trading day:
    existing DB data (last trading day) is preserved and no deploy is triggered.

CLOUDFLARE_API_TOKEN is read from .env only — never committed to git.
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

BASE              = Path(__file__).resolve().parent.parent
UV                = str(Path(os.environ.get("HOME", "")) / ".local/bin/uv")
MAX_RETRIES       = 3
RETRY_INTERVAL_S  = 3600  # 1 hour between retries


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


async def _fetch_with_retry(market: str, today: date) -> int:
    """Fetch rankings, retry on failure or empty result. Returns row count.

    Returns 0 after MAX_RETRIES exhausted — caller treats this as a holiday.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            print(f"  ▶ 抓取 {market} rankings ({today})"
                  + (f" [重試 {attempt}/{MAX_RETRIES}]" if attempt else "") + " …")
            n = await (fetch_us(today) if market == "US" else fetch_tw(today))
            if n > 0:
                print(f"  ✅ 儲存 {n} 筆 {market} ranking")
                return n

            # n=0: data not ready yet or holiday
            if attempt < MAX_RETRIES:
                wait_min = RETRY_INTERVAL_S // 60
                print(f"  ⚠ 取得 0 筆資料（API 尚未就緒？），"
                      f"{wait_min} 分鐘後重試（{attempt + 1}/{MAX_RETRIES}）…")
                await asyncio.sleep(RETRY_INTERVAL_S)
            else:
                print(f"  ⏭  {MAX_RETRIES} 次重試後仍無資料"
                      f"，判定為假日 — 保留最近交易日資料，不部署")

        except Exception as e:
            if attempt < MAX_RETRIES:
                wait_min = RETRY_INTERVAL_S // 60
                print(f"  ⚠ API 錯誤：{e}，"
                      f"{wait_min} 分鐘後重試（{attempt + 1}/{MAX_RETRIES}）…")
                await asyncio.sleep(RETRY_INTERVAL_S)
            else:
                print(f"  ❌ 重試 {MAX_RETRIES} 次後仍失敗：{e}")
                raise

    return 0


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

    if market == "US" and not _nyse_closed():
        now_et = datetime.now(ZoneInfo("America/New_York"))
        print(f"  ⏭  NYSE 尚未收盤（現在 ET: {now_et.strftime('%H:%M')}）— 跳過")
        return

    n = await _fetch_with_retry(market, today)
    if n == 0:
        return  # holiday or persistent API failure — keep existing data, skip deploy

    _rebuild_and_deploy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["TW", "US"], required=True)
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even if today's data already exists")
    args = parser.parse_args()
    asyncio.run(main(args.market, args.force))
