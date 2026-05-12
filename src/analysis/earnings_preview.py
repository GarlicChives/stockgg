#!/usr/bin/env python3
"""Earnings preview — pre-call analyst note for upcoming earnings events.

For each event in catalyst_events with:
    event_type = 'earnings'
    event_date BETWEEN tomorrow AND tomorrow+3 days
    preview_text IS NULL  (idempotent — skip already-generated)

Pulls last 30 days of articles + podcasts mentioning the ticker, sends to
Gemini to produce a structured pre-call note (market expectations, watch
points, risks, trade angle).

Stores the text directly on catalyst_events.preview_text. No new table.

Cost: 1 Gemini call per upcoming earnings. Typical 1-3 calls/day.
"""
from datetime import date

from src.prompts import render as render_prompt
from src.analysis.daily_report import _gemini_http

GEMINI_MODEL = "gemini-2.5-flash"
ARTICLE_TRUNC = 500
MAX_ARTICLES = 10


SCHEMA_SQL = [
    "ALTER TABLE catalyst_events ADD COLUMN IF NOT EXISTS preview_text TEXT",
    "ALTER TABLE catalyst_events ADD COLUMN IF NOT EXISTS preview_generated_at TIMESTAMPTZ",
]


async def ensure_schema(conn) -> None:
    for stmt in SCHEMA_SQL:
        await conn.execute(stmt)


async def _stock_name(conn, ticker: str, market: str | None) -> str:
    if not ticker:
        return ""
    row = await conn.fetchrow(
        """SELECT name FROM trading_rankings
           WHERE ticker=$1 ORDER BY rank_date DESC LIMIT 1""",
        ticker,
    )
    return (row["name"] or "").strip() if row else ""


async def _fetch_context(conn, ticker: str, name: str) -> list[dict]:
    rows = await conn.fetch(
        """SELECT id, title, source, published_at,
                  COALESCE(refined_content, content) AS body
           FROM articles
           WHERE status = 'active'
             AND (
                  $1 = ANY(tickers)
                  OR title ILIKE '%' || $1 || '%'
                  OR ($2 <> '' AND title ILIKE '%' || $2 || '%')
                  OR content ILIKE '%' || $1 || '%'
                  OR ($2 <> '' AND content ILIKE '%' || $2 || '%')
             )
             AND COALESCE(published_at, created_at) > NOW() - INTERVAL '30 days'
           ORDER BY GREATEST(
                    COALESCE(published_at, created_at),
                    COALESCE(created_at,   published_at)
                  ) DESC
           LIMIT $3""",
        ticker, name, MAX_ARTICLES,
    )
    return [dict(r) for r in rows]


async def generate_one(conn, event: dict, api_key: str) -> str | None:
    ticker = event["ticker"]
    if not ticker:
        return None
    name = await _stock_name(conn, ticker, event.get("market"))
    arts = await _fetch_context(conn, ticker, name)
    if not arts:
        return None

    lines = []
    for a in arts:
        body = (a.get("body") or "")[:ARTICLE_TRUNC].replace("\n", " ").strip()
        lines.append(f"- [{a['title'][:80]}] {body}")
    article_block = "\n".join(lines)

    ticker_label = f"{ticker} {name}".strip() if name else ticker
    event_date = event["event_date"]
    if hasattr(event_date, "strftime"):
        date_str = event_date.strftime("%Y-%m-%d")
    else:
        date_str = str(event_date)[:10]

    prompt = render_prompt(
        "earnings_preview",
        ticker_label=ticker_label,
        event_date=date_str,
        articles=article_block,
    )

    try:
        text = _gemini_http(api_key, GEMINI_MODEL, prompt, temperature=0.2, max_tokens=4096)
    except Exception as exc:
        print(f"  ⚠ [{ticker}] earnings preview failed: {exc}")
        return None
    return text.strip() or None


async def generate_previews(conn, api_key: str, days_ahead: int = 3,
                            max_calls: int = 5) -> int:
    """Find upcoming earnings without a preview yet, generate up to max_calls.
    Returns Gemini calls actually made.
    """
    await ensure_schema(conn)

    events = await conn.fetch(
        """SELECT id, event_date, ticker, market, title, importance
           FROM catalyst_events
           WHERE event_type = 'earnings'
             AND ticker <> ''
             AND preview_text IS NULL
             AND event_date >= CURRENT_DATE
             AND event_date <= CURRENT_DATE + ($1 || ' days')::interval
           ORDER BY importance DESC, event_date
           LIMIT $2""",
        str(days_ahead), max_calls,
    )
    if not events:
        print("  (no upcoming earnings need a preview)")
        return 0

    calls = 0
    for ev in events:
        text = await generate_one(conn, dict(ev), api_key)
        if not text:
            continue
        calls += 1
        await conn.execute(
            """UPDATE catalyst_events
                  SET preview_text = $1, preview_generated_at = NOW()
                WHERE id = $2""",
            text, ev["id"],
        )
        ev_date = ev["event_date"]
        date_str = ev_date.strftime("%m/%d") if hasattr(ev_date, "strftime") else str(ev_date)[5:10]
        print(f"  {date_str} {ev['ticker']:<6} preview generated ({len(text)} chars)")

    print(f"  Generated {calls}/{len(events)} previews (used {calls} Gemini call{'s' if calls != 1 else ''})")
    return calls
