#!/usr/bin/env python3
"""Startup / network-reconnect catch-up.

Detects missed scheduled jobs and re-runs them in order.
Safe to run multiple times — fully idempotent (DB-driven checks).

Triggered by launchd via three mechanisms:
  RunAtLoad     — on boot or agent (re)load
  WatchPaths    — when /private/var/run/resolv.conf changes (network up)
  StartInterval — every 30 min as a safety net

Job order mirrors the normal daily schedule:
  06:00  podcast-crawl    (RSS + Whisper, no Chrome needed)
  07:00  podcast-backfill (Gemini refinement for incomplete episodes)
  07:30  daily-briefing   (market data + AI report + market notes + HTML)

Article crawl (08:00 / 21:00) is intentionally skipped — it requires
Chrome on port 9222, which may not be running after reboot.
"""
import asyncio
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()

from src.utils import db

BASE = Path(__file__).resolve().parent.parent
LOG  = BASE / "logs"
UV   = "/Users/edward.song/.local/bin/uv"


def _run(args: list[str]) -> int:
    result = subprocess.run(args, cwd=BASE)
    return result.returncode


def _log_ran_today(log_path: Path) -> bool:
    """Check if a log file was written today (proxy for job completion)."""
    if not log_path.exists() or log_path.stat().st_size == 0:
        return False
    mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
    return mtime.date() == date.today()


async def _db_status() -> dict | None:
    """Query DB for what ran today. Returns None if network/DB not ready."""
    try:
        conn = await asyncio.wait_for(db.connect(), timeout=30)
    except Exception as e:
        print(f"  DB not reachable: {e}")
        return None

    today = date.today()
    try:
        report_date = await conn.fetchval("SELECT MAX(report_date) FROM analysis_reports")
        incomplete  = await conn.fetchval(
            """SELECT COUNT(*) FROM (
                 SELECT DISTINCT ON (title) content_tags
                 FROM articles
                 WHERE source LIKE 'podcast_%%'
                   AND status = 'active'
                   AND content IS NOT NULL
                   AND published_at >= CURRENT_DATE - 30
                 ORDER BY title, published_at DESC NULLS LAST
               ) d
               WHERE content_tags IS NULL OR content_tags = '{}'"""
        )
    finally:
        await conn.close()

    return {
        "briefing_today":     report_date == today if report_date else False,
        "podcast_incomplete": int(incomplete or 0),
    }


async def main():
    now  = datetime.now()
    hour = now.hour + now.minute / 60.0
    today_str = now.strftime("%Y-%m-%d %H:%M")
    print(f"=== IIA catch-up @ {today_str} ===")

    db = await _db_status()
    if db is None:
        print("Skipping — network/DB not ready yet")
        return

    print(f"  briefing_today={db['briefing_today']}  "
          f"podcast_incomplete={db['podcast_incomplete']}")

    did_something = False

    # ── 1. Podcast crawl (06:00) ──────────────────────────────────────
    if hour >= 6 and not _log_ran_today(LOG / "podcast_crawl.log"):
        print("\n▶ [MISSED] podcast-crawl — running now …")
        _run(["/usr/bin/caffeinate", "-is", UV, "run",
              "src/crawlers/podcasts.py", "--incremental"])
        did_something = True
    else:
        print("  ✓ podcast-crawl OK")

    # ── 2. Podcast backfill (07:00) ───────────────────────────────────
    if hour >= 7 and db["podcast_incomplete"] > 0:
        print(f"\n▶ [NEEDED] podcast-backfill ({db['podcast_incomplete']} episode(s)) …")
        _run([UV, "run", "scripts/podcast_backfill.py"])
        did_something = True
    else:
        print(f"  ✓ podcast-backfill OK (incomplete={db['podcast_incomplete']})")

    # ── 3. Daily briefing (07:30) ─────────────────────────────────────
    # daily_briefing.py now includes generate_html.py as its final step
    if hour >= 7.5 and not db["briefing_today"]:
        print("\n▶ [MISSED] daily-briefing — running now …")
        _run(["/usr/bin/caffeinate", "-is", UV, "run",
              "scripts/daily_briefing.py"])
        did_something = True
    elif did_something and db["briefing_today"]:
        # Briefing already ran today but we updated podcast data — rebuild HTML only
        print("\n▶ Rebuilding HTML (podcast data updated after today's briefing) …")
        _run([UV, "run", "scripts/generate_html.py"])
    else:
        print("  ✓ daily-briefing OK")

    print(f"\n{'✅ Catch-up complete' if did_something else '✅ All jobs current — nothing to catch up'}")


if __name__ == "__main__":
    asyncio.run(main())
