#!/usr/bin/env python3
"""Weekly theme deep-dive — once a week (Sunday), write a 600-900 字
產業研究 report on the 1-2 hottest themes from this week's market_notes.

Input: latest analysis_reports.market_notes_json["topics"]. The topics are
already Gemini-curated cross-source themes; we just pick the top 2 by
sentiment confidence + key_points count.

Output: weekly_theme_reports (one row per (report_week, theme_name)).

Idempotent — if a report for ISO-week of today already exists, skip.

Cost: 1-2 Gemini calls/week.
"""
import json
from datetime import date, timedelta

from src.prompts import render as render_prompt
from src.analysis.daily_report import _gemini_http

GEMINI_MODEL = "gemini-2.5-flash"
ARTICLE_TRUNC = 600
MAX_ARTICLES = 8


SCHEMA_SQL = [
    """CREATE TABLE IF NOT EXISTS weekly_theme_reports (
        id              SERIAL PRIMARY KEY,
        report_week     DATE NOT NULL,
        theme_name      TEXT NOT NULL,
        theme_summary   TEXT,
        report_text     TEXT NOT NULL,
        related_tickers TEXT[],
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(report_week, theme_name)
    )""",
    "CREATE INDEX IF NOT EXISTS weekly_theme_reports_week_idx ON weekly_theme_reports(report_week DESC)",
]


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def ensure_schema(conn) -> None:
    for stmt in SCHEMA_SQL:
        await conn.execute(stmt)


async def _fetch_topic_articles(conn, theme_name: str, tickers: list[str]) -> list[dict]:
    """Pull articles + podcast notes that probably belong to this theme."""
    name_clause = "title ILIKE '%' || $1 || '%' OR content ILIKE '%' || $1 || '%'"
    ticker_clause = ""
    params: list = [theme_name]
    if tickers:
        params.append(tickers)
        ticker_clause = " OR tickers && $2::text[]"
    rows = await conn.fetch(
        f"""SELECT id, title, source, published_at,
                   COALESCE(refined_content, content) AS body
           FROM articles
           WHERE status = 'active'
             AND ({name_clause}{ticker_clause})
             AND COALESCE(published_at, created_at) > NOW() - INTERVAL '7 days'
           ORDER BY GREATEST(
                    COALESCE(published_at, created_at),
                    COALESCE(created_at,   published_at)
                  ) DESC
           LIMIT {MAX_ARTICLES}""",
        *params,
    )
    return [dict(r) for r in rows]


def _pick_top_topics(market_notes: dict, n: int = 2) -> list[dict]:
    topics = (market_notes or {}).get("topics", []) or []
    # Rank by (key_points length × source diversity), tie-break by name length
    def _score(t: dict) -> tuple:
        kp = len(t.get("key_points", []) or [])
        srcs = len(t.get("sources", []) or [])
        return (-(kp * 2 + srcs), t.get("topic", ""))
    sorted_topics = sorted(topics, key=_score)
    return sorted_topics[:n]


async def generate_one(conn, topic: dict, api_key: str) -> str | None:
    theme_name = topic.get("topic", "").strip()
    if not theme_name:
        return None

    tickers = [str(t).strip() for t in (topic.get("tickers", []) or []) if t]
    arts = await _fetch_topic_articles(conn, theme_name, tickers)

    article_lines = []
    for a in arts:
        body = (a.get("body") or "")[:ARTICLE_TRUNC].replace("\n", " ").strip()
        article_lines.append(f"- [{a['title'][:80]}] {body}")
    articles_block = "\n".join(article_lines) or "(本週相關報導不足)"

    summary = (topic.get("summary") or "").strip()
    kps = topic.get("key_points", []) or []
    if kps:
        summary += "\n關鍵點：" + " / ".join(kps[:5])

    prompt = render_prompt(
        "weekly_theme_report",
        theme_name=theme_name,
        theme_summary=summary or "(無摘要)",
        articles=articles_block,
        tickers=", ".join(tickers[:12]) or "(無)",
    )

    try:
        text = _gemini_http(api_key, GEMINI_MODEL, prompt, temperature=0.25, max_tokens=8192)
    except Exception as exc:
        print(f"  ⚠ [{theme_name}] weekly report failed: {exc}")
        return None
    return text.strip() or None


async def generate_weekly_themes(conn, api_key: str, top_n: int = 2,
                                  force: bool = False) -> int:
    """Pick top N hot themes from latest market_notes and write deep-dives.
    Skips if a report for the current ISO-week already exists per theme.
    Returns Gemini calls made.
    """
    await ensure_schema(conn)

    week = _monday_of(date.today())

    row = await conn.fetchrow(
        """SELECT market_notes_json FROM analysis_reports
           WHERE market_notes_json IS NOT NULL
           ORDER BY report_date DESC LIMIT 1"""
    )
    if not row:
        print("  (no market_notes available for weekly theme)")
        return 0

    mn = row["market_notes_json"]
    market_notes = mn if isinstance(mn, dict) else json.loads(mn)
    topics = _pick_top_topics(market_notes, n=top_n)
    if not topics:
        print("  (market_notes has no topics)")
        return 0

    calls = 0
    for topic in topics:
        theme_name = (topic.get("topic") or "").strip()
        if not theme_name:
            continue
        if not force:
            exists = await conn.fetchval(
                "SELECT 1 FROM weekly_theme_reports WHERE report_week=$1 AND theme_name=$2",
                week, theme_name,
            )
            if exists:
                print(f"  ⏭  本週 [{theme_name}] 已有報告 — 跳過")
                continue
        text = await generate_one(conn, topic, api_key)
        if not text:
            continue
        calls += 1
        tickers = [str(t).strip() for t in (topic.get("tickers", []) or []) if t]
        await conn.execute(
            """INSERT INTO weekly_theme_reports
               (report_week, theme_name, theme_summary, report_text, related_tickers)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (report_week, theme_name)
               DO UPDATE SET theme_summary=$3, report_text=$4, related_tickers=$5""",
            week, theme_name, topic.get("summary", "")[:1000], text, tickers,
        )
        print(f"  ✓ [{theme_name}] weekly report saved ({len(text)} chars)")

    print(f"  Generated {calls} weekly theme report{'s' if calls != 1 else ''}")
    return calls
