#!/usr/bin/env python3
"""Thesis tracker — judge whether today's news supports or contradicts
each active watchlist thesis.

For each watchlist item with a thesis:
  1. Pull articles + podcast notes from last 24h that mention the ticker
  2. Ask Gemini for verdict (supportive / neutral / contradicting) + summary
  3. Persist to thesis_signals (one row per watchlist × check_date)

Skips silently if no fresh articles mention the ticker (no quota burn).
Caller passes max_calls to cap Gemini usage.
"""
import json
import re
from datetime import date

from src.prompts import render as render_prompt
from src.analysis.daily_report import _gemini_http

GEMINI_MODEL = "gemini-2.5-flash"
ARTICLE_TRUNC = 600
MAX_ARTICLES_PER_TICKER = 6


SCHEMA_SQL = [
    "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS thesis TEXT",
    "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS thesis_added_at TIMESTAMPTZ",
    "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS target_price NUMERIC",
    "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ",
    """CREATE TABLE IF NOT EXISTS thesis_signals (
        id            SERIAL PRIMARY KEY,
        watchlist_id  BIGINT REFERENCES watchlist(id) ON DELETE CASCADE,
        check_date    DATE NOT NULL,
        verdict       TEXT NOT NULL,
        summary       TEXT,
        key_evidence  TEXT[],
        article_ids   INT[],
        created_at    TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(watchlist_id, check_date)
    )""",
    "CREATE INDEX IF NOT EXISTS thesis_signals_date_idx ON thesis_signals(check_date)",
]


async def ensure_schema(conn) -> None:
    for stmt in SCHEMA_SQL:
        await conn.execute(stmt)


def _parse_response(raw: str) -> dict | None:
    if not raw:
        return None
    cleaned = raw.strip()
    # Strip leading ```json (or just ```) and trailing ``` fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    verdict = (obj.get("verdict") or "").lower()
    if verdict not in {"supportive", "neutral", "contradicting"}:
        return None
    return {
        "verdict": verdict,
        "summary": (obj.get("summary") or "").strip()[:800],
        "key_evidence": [s.strip() for s in (obj.get("key_evidence") or []) if s][:5],
    }


async def _fetch_recent_articles(conn, ticker: str, name: str | None = None) -> list[dict]:
    # Articles + podcast notes that mention this thesis. Podcast tickers[] is
    # often empty because extract_tickers's regex misses Chinese names like
    # 「台積電」 or "NVIDIA" (it only catches 4-digit TW codes and (XXX) US
    # parenthesized form). So we ALSO scan title + content for the ticker and
    # the user-provided name.
    name = (name or "").strip()
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
             AND GREATEST(
                   COALESCE(published_at, created_at),
                   COALESCE(created_at,   published_at)
                 ) > NOW() - INTERVAL '7 days'
           ORDER BY GREATEST(
                   COALESCE(published_at, created_at),
                   COALESCE(created_at,   published_at)
                 ) DESC
           LIMIT $3""",
        ticker, name, MAX_ARTICLES_PER_TICKER,
    )
    return [dict(r) for r in rows]


async def check_one(conn, item: dict, api_key: str) -> dict | None:
    ticker = item["ticker"]
    thesis = item.get("thesis") or ""
    if not thesis:
        return None
    arts = await _fetch_recent_articles(conn, ticker, item.get("name"))
    if not arts:
        return None

    article_lines = []
    for a in arts:
        body = (a.get("body") or "")[:ARTICLE_TRUNC].replace("\n", " ").strip()
        article_lines.append(f"- [{a['title'][:80]}] {body}")
    articles_block = "\n".join(article_lines)

    ticker_label = ticker
    if item.get("name"):
        ticker_label = f"{ticker} {item['name']}"

    prompt = render_prompt(
        "thesis_check",
        ticker_label=ticker_label,
        thesis=thesis,
        articles=articles_block,
    )

    try:
        raw = _gemini_http(api_key, GEMINI_MODEL, prompt, temperature=0.1, max_tokens=4096)
    except Exception as exc:
        print(f"  ⚠ [{ticker}] Gemini call failed: {exc}")
        return None

    parsed = _parse_response(raw)
    if not parsed:
        print(f"  ⚠ [{ticker}] unparseable response (len={len(raw)}):\n----\n{raw}\n----")
        return None

    parsed["article_ids"] = [a["id"] for a in arts]
    return parsed


async def check_all_theses(conn, api_key: str, max_calls: int = 10) -> int:
    """Loop active watchlist items, check each, persist signals.
    Returns Gemini calls actually made.
    """
    await ensure_schema(conn)

    items = await conn.fetch(
        """SELECT id, ticker, market, name, thesis
           FROM watchlist
           WHERE is_active = TRUE AND thesis IS NOT NULL AND thesis != ''
           ORDER BY last_checked_at NULLS FIRST, id
           LIMIT $1""",
        max_calls,
    )
    if not items:
        print("  (no active theses to check)")
        return 0

    today = date.today()
    calls = 0
    for item in items:
        result = await check_one(conn, dict(item), api_key)
        if result is None:
            continue
        calls += 1
        await conn.execute(
            """INSERT INTO thesis_signals
               (watchlist_id, check_date, verdict, summary, key_evidence, article_ids)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (watchlist_id, check_date)
               DO UPDATE SET verdict=$3, summary=$4, key_evidence=$5, article_ids=$6""",
            item["id"], today,
            result["verdict"], result["summary"],
            result["key_evidence"], result["article_ids"],
        )
        await conn.execute(
            "UPDATE watchlist SET last_checked_at=NOW() WHERE id=$1", item["id"]
        )
        verdict_zh = {"supportive": "✅ 支持", "neutral": "▫ 中立", "contradicting": "⚠ 矛盾"}[result["verdict"]]
        print(f"  {item['ticker']:<6} → {verdict_zh}  {result['summary'][:60]}")

    print(f"  Checked {calls}/{len(items)} theses (used {calls} Gemini call{'s' if calls != 1 else ''})")
    return calls
