#!/usr/bin/env python3
"""Standalone market notes runner — 18:00 & 23:00 daily.

Skips analysis if no new articles have arrived since the last run,
avoiding redundant Gemini calls. On success, rebuilds HTML and deploys.
CLOUDFLARE_API_TOKEN is read from .env only — never committed to git.
"""
import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.utils import db
from src.analysis.market_notes import generate_market_notes

BASE = Path(__file__).resolve().parent.parent
UV   = str(Path(os.environ.get("HOME", "")) / ".local/bin/uv")


def _rebuild_and_deploy() -> None:
    print("  ▶ 重建 HTML …")
    subprocess.run([UV, "run", "scripts/generate_html.py"], cwd=BASE, check=True)
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        print("  ⚠ CLOUDFLARE_API_TOKEN 未設定，跳過部署")
        return
    print("  ▶ 部署至 Cloudflare …")
    try:
        subprocess.run(
            ["npx", "wrangler", "deploy"],
            cwd=BASE, check=True,
            env={**os.environ, "CLOUDFLARE_API_TOKEN": token},
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # Local wrangler may fail (e.g. Node version mismatch). The DB write
        # already succeeded above; CI's 07:30 cron will redeploy from origin.
        print(f"  ⚠ Cloudflare 部署失敗（{type(exc).__name__}）— DB 已更新，CI cron 會代為部署")


async def main(force: bool = False) -> None:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("  ⏭  GOOGLE_API_KEY 未設定 — 跳過")
        return

    conn = await db.connect()
    try:
        await conn.execute(
            "ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS market_notes_run_at TIMESTAMPTZ"
        )

        if not force:
            last_run = await conn.fetchval(
                "SELECT MAX(market_notes_run_at) FROM analysis_reports"
            )
            has_new = await conn.fetchval(
                """SELECT 1 FROM articles
                   WHERE status = 'active'
                     AND ($1::timestamptz IS NULL OR created_at > $1)
                   LIMIT 1""",
                last_run,
            )
            if not has_new:
                print(f"  ⏭  自上次分析後無新資料（上次: {last_run}）— 跳過")
                return
            print(f"  ▶ 偵測到新資料（上次分析: {last_run}），執行跨來源議題分析…")
        else:
            print("  ▶ --force 模式，強制執行跨來源議題分析…")

        await generate_market_notes(conn, date.today(), api_key)
    finally:
        await conn.close()

    _rebuild_and_deploy()


if __name__ == "__main__":
    asyncio.run(main(force="--force" in sys.argv))
