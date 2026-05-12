#!/usr/bin/env python3
"""Full daily briefing pipeline.

1. Fetch market data (indices, VIX, 10Y, Fear & Greed)
2. Fetch US + TW trading value rankings (TWSE + TPEX merged)
3. Generate M3 analysis report via Gemini
4. Generate cross-source market notes via Gemini
5. Cleanup articles/news older than 180 days

Idempotency:
  Steps 4 & 5 hit Gemini (paid). On re-run within the same date, they skip if
  the row already has output, to avoid wasting quota and overwriting a good
  result with a potentially noisier one. Use --force to override (e.g. after
  changing prompt or model).

Usage:
    uv run scripts/daily_briefing.py              # full pipeline (skip if today done)
    uv run scripts/daily_briefing.py --skip-fetch # skip Steps 1-3 (use DB data)
    uv run scripts/daily_briefing.py --force      # force re-run paid steps
"""
import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from src.news.market_data import fetch_and_store as fetch_indicators
from src.news.us_rankings import fetch_and_store as fetch_us
from src.news.tw_rankings import fetch_and_store as fetch_tw
from src.news.catalyst_calendar import fetch_and_store as fetch_catalysts
from src.analysis.daily_report import generate_report
from src.utils import db


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

    result = await conn.execute(
        "DELETE FROM catalyst_events WHERE event_date < CURRENT_DATE - INTERVAL '14 days'"
    )
    cat_del = int((result.split()[-1]) if result else 0)

    if art_del or pod_del or news_del or cat_del:
        print(f"  Cleaned up: {art_del} articles, {pod_del} podcasts, {news_del} news items, {cat_del} catalyst events")


async def _existing_today(conn, today: date) -> tuple[bool, bool]:
    """Return (has_report, has_notes) for today's analysis_reports row."""
    row = await conn.fetchrow(
        "SELECT raw_response IS NOT NULL AS has_report, "
        "       market_notes_json IS NOT NULL AS has_notes "
        "FROM analysis_reports WHERE report_date=$1",
        today,
    )
    return (bool(row and row["has_report"]),
            bool(row and row["has_notes"]))


async def main():
    skip_fetch = "--skip-fetch" in sys.argv
    force      = "--force"      in sys.argv
    today = date.today()

    if not skip_fetch:
        print("── Step 1: Market Indicators ──")
        await fetch_indicators(today)
        print()

        _ck = await db.connect()
        _has_us = await _ck.fetchval(
            "SELECT 1 FROM trading_rankings WHERE rank_date=$1 AND market='US' LIMIT 1", today)
        _has_tw = await _ck.fetchval(
            "SELECT 1 FROM trading_rankings WHERE rank_date=$1 AND market='TW' LIMIT 1", today)
        await _ck.close()

        print("── Step 2: US Trading Rankings ──")
        if _has_us and not force:
            print("  ⏭  今日 US ranking 已存在 — 跳過")
        else:
            await fetch_us(today)
        print()
        print("── Step 3: TW Trading Rankings (TWSE + TPEX) ──")
        if _has_tw and not force:
            print("  ⏭  今日 TW ranking 已存在 — 跳過")
        else:
            await fetch_tw()
        print()

    if not skip_fetch:
        print("── Step 3.5: Catalyst Calendar (earnings + macro events) ──")
        try:
            await fetch_catalysts()
        except Exception as exc:
            print(f"  ⚠ catalyst calendar 抓取失敗（{exc}）— 不影響後續步驟")
        print()

    conn = await db.connect()
    try:
        has_report, has_notes = await _existing_today(conn, today)
    finally:
        await conn.close()

    print("── Step 4: M3 Analysis Report ──")
    if has_report and not force:
        print(f"  ⏭  今日已有日報（{today}）— 跳過以節省 Gemini quota。"
              f" 如改了 prompt/model 需重產，加 --force")
    else:
        report = await generate_report(today)
        print()
        print("=" * 60)
        print(report)
        print("=" * 60)
    print()

    # Step 5 moved to dedicated schedule (run_market_notes.py at 18:00 & 23:00)
    print("── Step 5: Cross-Source Market Notes — 由獨立排程執行（18:00 / 23:00）──")
    print()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        print("── Step 5.5: Theme Dictionary (Search+LLM Classification) ──")
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
    conn = await db.connect()
    try:
        await cleanup_old_data(conn)
    finally:
        await conn.close()
    print()

    print("── Step 7: Rebuild HTML ──")
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
