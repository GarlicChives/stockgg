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

from src.utils.api_logger import log_usage

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models"

LOOKBACK_DAYS = 7
MAX_ARTICLES  = 40
BODY_CHARS    = 800

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
            "maxOutputTokens": 8192,
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

    return f"""你是資深投資研究員，請分析以下近 {LOOKBACK_DAYS} 天的台灣訂閱專欄與 Podcast 逐字稿，\
找出「兩個或以上不同來源」同時提及的投資議題或標的。

重要規則：
- 只列出真正有「跨來源共識」的議題（至少 2 個不同 source 都提到）
- 議題名稱要簡潔具體（如：記憶體漲價、EMIB 封裝概念、台積電資金輪動）
- 標的名稱用台股：公司名+代號（如：旺宏(6670)）、美股：TICKER(US)
- 每個議題必須能明確指出哪 2 個以上來源提到

=== 文章內容 ===
{art_text}

請以 JSON 格式輸出，不要有任何說明文字，直接輸出 JSON：
{{
  "topics": [
    {{
      "topic": "議題名稱",
      "sentiment": "偏多|中立|偏空",
      "sources": ["source_name1", "source_name2"],
      "tickers": ["旺宏(6670)", "南亞科(2408)", "MU(US)"],
      "summary": "50字以內的關鍵摘要",
      "key_points": ["重點1", "重點2", "重點3"],
      "articles": [
        {{"source": "source_name", "title": "文章標題", "date": "YYYY-MM-DD"}}
      ]
    }}
  ]
}}
"""


async def generate_market_notes(conn, report_date: date, api_key: str) -> dict:
    """Fetch articles, call Gemini, store and return cross-source topics."""

    # Ensure column exists
    await conn.execute(
        "ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS market_notes_json JSONB"
    )

    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    rows = await conn.fetch(
        f"""SELECT source, title, published_at,
                  LEFT(COALESCE(refined_content, content), {BODY_CHARS}) AS body
           FROM articles
           WHERE published_at >= $1 AND status='active'
             AND COALESCE(LENGTH(refined_content), LENGTH(content), 0) > 100
           ORDER BY published_at DESC
           LIMIT {MAX_ARTICLES}""",
        cutoff,
    )

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

    print(f"  Market notes: {len(articles)} articles → Gemini…")
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
        """INSERT INTO analysis_reports (report_date, market_notes_json)
           VALUES ($1, $2::jsonb)
           ON CONFLICT (report_date) DO UPDATE
           SET market_notes_json = EXCLUDED.market_notes_json""",
        report_date,
        json.dumps(notes),
    )
    n = len(notes.get("topics", []))
    print(f"  Stored {n} cross-source topics")
    return notes
