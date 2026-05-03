#!/usr/bin/env python3
"""Build (or refresh) the investment theme dictionary from DB content.

Reads all refined articles (up to 180 days) + refined podcasts (30 days),
sends to Gemini, and writes data/theme_dictionary.json.

Run manually after initial setup, or monthly to refresh:
    uv run python scripts/build_theme_dictionary.py
"""
import asyncio
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()

from src.utils.api_logger import log_usage

import asyncpg

DICT_FILE    = Path(__file__).resolve().parents[1] / "data" / "theme_dictionary.json"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models"

ARTICLE_TRUNC  = 250   # chars per article in the prompt
PODCAST_TRUNC  = 400   # chars per podcast episode
PODCAST_DAYS   = 30    # podcast window for dictionary build


_PROMPT_TEMPLATE = """\
你是一位專精台美股市的資深投資分析師。以下是從台灣主流投資分析平台及 Podcast 收集的精煉投資內容（近半年文章 + 近一個月 Podcast）。

【任務】
分析所有內容，建立一份「台美股投資主題字典」。

【顆粒度要求 — 非常重要】
主題必須「細粒度」，不可籠統：
✅ 正確：HBM記憶體、載板/ABF基板、光通訊模組（800G/1.6T）、MLCC積層陶瓷電容、石英晶體元件、PCB鑽針、液冷散熱、氣冷散熱、台積電CoWoS先進封裝、台積電條款（ETF持股上限）、CoWoS載板、電動車電池管理IC、車用SiC功率元件、AI推論晶片、伺服器電源（VR模組）
❌ 錯誤：半導體（太廣）、科技股（太廣）、電子業（太廣）

【輸出規則】
1. keywords：中英文混合，含技術術語、新聞常見用詞（例：「HBM」「高頻寬記憶體」「AI記憶體」「SK Hynix」）
2. tw_stocks：格式 {{"code":"2330","name":"台積電"}}。不限文章中出現的標的，請補全你知道的同族群重要台股
3. us_stocks：格式 {{"ticker":"NVDA","name":"Nvidia"}}。同上，補全重要美股
4. id：snake_case 英文
5. 只輸出 JSON，不要任何說明文字

=== 投資內容 ===
{content}
=== 內容結束 ===

輸出格式（嚴格遵守）：
{{
  "themes": [
    {{
      "id": "hbm_memory",
      "name": "HBM記憶體",
      "keywords": ["HBM", "高頻寬記憶體", "HBM3e", "AI記憶體", "SK Hynix"],
      "tw_stocks": [{{"code": "2408", "name": "南亞科"}}, {{"code": "4238", "name": "華邦電"}}],
      "us_stocks": [{{"ticker": "MU", "name": "Micron"}}, {{"ticker": "WDC", "name": "Western Digital"}}]
    }}
  ]
}}"""


async def _fetch_content(conn) -> tuple[list[dict], list[dict]]:
    """Fetch refined articles and podcasts from DB."""
    articles = await conn.fetch(
        f"""SELECT source, title, published_at,
                  LEFT(refined_content, {ARTICLE_TRUNC}) AS body
           FROM articles
           WHERE status='active'
             AND source NOT LIKE 'podcast_%'
             AND refined_content IS NOT NULL
             AND LENGTH(refined_content) > 50
           ORDER BY published_at DESC
           LIMIT 200"""
    )

    cutoff = date.today() - timedelta(days=PODCAST_DAYS)
    podcasts = await conn.fetch(
        f"""SELECT source, title, published_at,
                  LEFT(refined_content, {PODCAST_TRUNC}) AS body
           FROM articles
           WHERE status='active'
             AND source LIKE 'podcast_%'
             AND refined_content IS NOT NULL
             AND LENGTH(refined_content) > 50
             AND published_at >= $1
           ORDER BY published_at DESC""",
        cutoff,
    )
    return [dict(r) for r in articles], [dict(r) for r in podcasts]


def _build_content_block(articles: list[dict], podcasts: list[dict]) -> str:
    lines = []
    for r in articles:
        dt = str(r["published_at"])[:10] if r["published_at"] else "?"
        lines.append(f"[{dt}|{r['source']}] {r['title']}\n{r['body']}")
    lines.append("\n--- Podcast ---")
    for r in podcasts:
        dt = str(r["published_at"])[:10] if r["published_at"] else "?"
        lines.append(f"[{dt}|{r['source']}] {r['title']}\n{r['body']}")
    return "\n---\n".join(lines)


def _call_gemini(api_key: str, prompt: str) -> str:
    url = f"{GEMINI_BASE}/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 32000,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    usage = data.get("usageMetadata", {})
    log_usage(
        "gemini", GEMINI_MODEL, "build_theme_dict",
        usage.get("promptTokenCount", 0),
        usage.get("candidatesTokenCount", 0),
        usage.get("thoughtsTokenCount", 0),
    )
    parts = data["candidates"][0]["content"]["parts"]
    text = "".join(
        p["text"] for p in parts if "text" in p and not p.get("thought", False)
    ).strip()
    return text or "".join(p.get("text", "") for p in parts).strip()


def _extract_theme_objects(text: str) -> list[dict]:
    """Extract complete JSON theme objects even from a truncated response."""
    # Find start of themes array
    start_bracket = text.find('"themes"')
    if start_bracket == -1:
        return []
    bracket = text.find('[', start_bracket)
    if bracket == -1:
        return []

    results = []
    depth = 0
    obj_start = None
    in_string = False
    escape_next = False

    for i, c in enumerate(text[bracket + 1:], start=bracket + 1):
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if c == '{':
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    obj = json.loads(text[obj_start:i + 1])
                    if "id" in obj and "name" in obj:
                        results.append(obj)
                except Exception:
                    pass
                obj_start = None

    return results


def _parse_themes(raw: str) -> list[dict]:
    raw = raw.strip()
    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw[raw.find("{"):] if "{" in raw else raw

    # Try full parse first (response not truncated)
    try:
        data = json.loads(raw)
        themes = data.get("themes", [])
        if themes:
            return themes
    except Exception:
        pass

    # Fallback: extract individual complete objects (handles truncation)
    return _extract_theme_objects(raw)


async def main():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("❌ GOOGLE_API_KEY not set")
        return

    print("Connecting to DB…")
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    articles, podcasts = await _fetch_content(conn)
    await conn.close()

    print(f"Fetched {len(articles)} articles + {len(podcasts)} podcast episodes")
    content_block = _build_content_block(articles, podcasts)
    prompt = _PROMPT_TEMPLATE.format(content=content_block)
    chars = len(prompt)
    tokens_est = chars // 3
    print(f"Prompt size: {chars:,} chars (~{tokens_est:,} tokens) → sending to {GEMINI_MODEL}…")

    try:
        raw = _call_gemini(api_key, prompt)
    except Exception as e:
        print(f"❌ Gemini error: {e}")
        return

    themes = _parse_themes(raw)
    if not themes:
        print("❌ Could not parse themes from response")
        print("Raw response (first 500 chars):", raw[:500])
        return

    print(f"✅ Parsed {len(themes)} themes")
    for t in themes:
        tw = len(t.get("tw_stocks", []))
        us = len(t.get("us_stocks", []))
        kw = len(t.get("keywords", []))
        print(f"  [{t['id']}] {t['name']} — {kw} keywords, {tw} TW stocks, {us} US stocks")

    output = {
        "meta": {
            "version": "1",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": GEMINI_MODEL,
            "source_articles": len(articles),
            "source_podcasts": len(podcasts),
        },
        "themes": themes,
    }

    DICT_FILE.parent.mkdir(parents=True, exist_ok=True)
    DICT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ Written to {DICT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
