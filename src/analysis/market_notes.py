#!/usr/bin/env python3
"""Cross-source topic intersection using Gemini.

Queries last 7 days of articles from all sources, sends to Gemini to identify
topics discussed by 2+ different sources. Stores result as market_notes_json
in analysis_reports.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.prompts import render as render_prompt
from src.utils.api_logger import log_usage

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models"

PODCAST_EPISODES_PER_SOURCE = 4   # 每個節目取最近幾集
ARTICLE_LOOKBACK_DAYS       = 90  # 訂閱文章回溯 3 個月
BODY_CHARS                  = 800

SOURCE_NAMES = {
    "macromicro":          "財經M平方",
    "vocus":               "韭菜王",
    "statementdog":        "財報狗",
    "investanchors":       "投資錨點",
    "pressplay":           "財經捕手",
    "podcast_gooaye":      "股癌 Gooaye",
    "podcast_macromicro":  "財經M平方 podcast",
    "podcast_chives_grad": "韭菜畢業班",
    "podcast_stock_barrel":"股海飯桶",
    "podcast_zhaohua":        "兆華與股惑仔",
    "podcast_statementdog":   "財報狗 podcast",
}


def _gemini_http(api_key: str, prompt: str) -> str:
    url = f"{GEMINI_BASE}/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 32768,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    usage = data.get("usageMetadata", {})
    log_usage(
        "gemini", GEMINI_MODEL, "market_notes",
        usage.get("promptTokenCount", 0),
        usage.get("candidatesTokenCount", 0),
        usage.get("thoughtsTokenCount", 0),
    )
    parts = data["candidates"][0]["content"]["parts"]
    text = "".join(
        p["text"] for p in parts if "text" in p and not p.get("thought", False)
    ).strip()
    return text or "".join(p.get("text", "") for p in parts).strip()


def _build_prompt(articles: list[dict]) -> str:
    art_text = "\n---\n".join(
        f"[{a['date']}|{a['source_name']}] {a['title']}\n{a['body']}"
        for a in articles
    )
    return render_prompt("market_notes", articles=art_text)


async def generate_market_notes(conn, report_date: date, api_key: str) -> dict:
    """Fetch articles, call Gemini, store and return cross-source topics."""

    # Ensure column exists
    await conn.execute(
        "ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS market_notes_json JSONB"
    )

    podcast_rows = await conn.fetch(
        f"""SELECT source, title, published_at,
                   LEFT(COALESCE(refined_content, content), {BODY_CHARS}) AS body
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY source ORDER BY published_at DESC
                ) AS rn
                FROM articles
                WHERE source LIKE 'podcast_%' AND status='active'
                  AND content_tags != '{{}}'
                  AND COALESCE(LENGTH(refined_content), LENGTH(content), 0) > 100
            ) t
            WHERE rn <= {PODCAST_EPISODES_PER_SOURCE}"""
    )

    article_cutoff = date.today() - timedelta(days=ARTICLE_LOOKBACK_DAYS)
    article_rows = await conn.fetch(
        f"""SELECT source, title, published_at,
                   LEFT(COALESCE(refined_content, content), {BODY_CHARS}) AS body
            FROM articles
            WHERE source NOT LIKE 'podcast_%' AND status='active'
              AND published_at >= $1
              AND COALESCE(LENGTH(refined_content), LENGTH(content), 0) > 100
            ORDER BY published_at DESC""",
        article_cutoff,
    )

    rows = list(podcast_rows) + list(article_rows)

    articles = []
    for r in rows:
        src = r["source"] or ""
        articles.append({
            "source_name": SOURCE_NAMES.get(src, src),
            "title": r["title"] or "",
            "date": str(r["published_at"])[:10] if r["published_at"] else "?",
            "body": r["body"] or "",
        })

    if not articles:
        return {"topics": []}

    print(f"  Market notes: {len(podcast_rows)} podcast eps + {len(article_rows)} articles → Gemini…")
    raw = _gemini_http(api_key, _build_prompt(articles))

    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[raw.find("{"):raw.rfind("}") + 1]

    if not raw:
        print(f"  ⚠ Gemini returned empty response — saving empty topics")
        notes = {"topics": []}
    else:
        try:
            notes = json.loads(raw)
        except Exception as e:
            # Try harder to extract JSON block
            m = re.search(r'\{[\s\S]+\}', raw)
            if m:
                try:
                    notes = json.loads(m.group(0))
                except Exception:
                    notes = {"topics": []}
            else:
                print(f"  ⚠ JSON parse error: {e} — saving empty topics")
                notes = {"topics": []}

    await conn.execute(
        "ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS market_notes_run_at TIMESTAMPTZ"
    )
    await conn.execute(
        """INSERT INTO analysis_reports (report_date, market_notes_json, market_notes_run_at)
           VALUES ($1, $2::jsonb, NOW())
           ON CONFLICT (report_date) DO UPDATE
           SET market_notes_json    = EXCLUDED.market_notes_json,
               market_notes_run_at  = NOW()""",
        report_date,
        json.dumps(notes),
    )
    n = len(notes.get("topics", []))
    print(f"  Stored {n} cross-source topics")
    return notes
