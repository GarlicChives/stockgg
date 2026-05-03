#!/usr/bin/env python3
"""Investment theme dictionary builder — append-only, Gemini-powered.

Two modes:
  Full rebuild (manual, first-time):
      uv run python scripts/build_theme_dictionary.py --rebuild

  Incremental append (called after each crawl cycle):
      uv run python scripts/build_theme_dictionary.py
      OR imported: await append_new_themes(conn, api_key)

Schema per theme (maps 1:1 to future DB columns):
  id          TEXT PRIMARY KEY  — snake_case slug
  name        TEXT              — display name
  keyword     TEXT              — single precise match term (used for article matching)
  tw_stocks   JSONB             — [{"code":"2330","name":"台積電"}]
  us_stocks   JSONB             — [{"ticker":"NVDA","name":"Nvidia"}]

DB migration: replace _load_dict() / _save_dict() implementations only.
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

ARTICLE_TRUNC  = 250
PODCAST_TRUNC  = 400
PODCAST_DAYS   = 30


# ── Dictionary I/O (swap for DB migration) ────────────────────────────────────

def _load_dict() -> dict:
    if not DICT_FILE.exists():
        return {"meta": {}, "themes": []}
    with DICT_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def _save_dict(data: dict) -> None:
    DICT_FILE.parent.mkdir(parents=True, exist_ok=True)
    DICT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Gemini call ───────────────────────────────────────────────────────────────

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


# ── JSON parsing (handles truncated responses) ────────────────────────────────

def _extract_theme_objects(text: str) -> list[dict]:
    """Extract complete JSON theme objects even from a truncated response."""
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
                    if "id" in obj and "name" in obj and "keyword" in obj:
                        results.append(obj)
                except Exception:
                    pass
                obj_start = None
    return results


def _parse_themes(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[raw.find("{"):] if "{" in raw else raw
    try:
        data = json.loads(raw)
        themes = data.get("themes", [])
        if themes:
            return themes
    except Exception:
        pass
    return _extract_theme_objects(raw)


# ── DB fetch helpers ──────────────────────────────────────────────────────────

async def _fetch_all(conn) -> tuple[list[dict], list[dict]]:
    """Full rebuild: all refined articles + 30-day podcasts."""
    articles = await conn.fetch(
        f"""SELECT source, title, published_at,
                  LEFT(refined_content, {ARTICLE_TRUNC}) AS body
           FROM articles
           WHERE status='active' AND source NOT LIKE 'podcast_%'
             AND refined_content IS NOT NULL AND LENGTH(refined_content) > 50
           ORDER BY published_at DESC LIMIT 200"""
    )
    cutoff = date.today() - timedelta(days=PODCAST_DAYS)
    podcasts = await conn.fetch(
        f"""SELECT source, title, published_at,
                  LEFT(refined_content, {PODCAST_TRUNC}) AS body
           FROM articles
           WHERE status='active' AND source LIKE 'podcast_%'
             AND refined_content IS NOT NULL AND LENGTH(refined_content) > 50
             AND published_at >= $1
           ORDER BY published_at DESC""",
        cutoff,
    )
    return [dict(r) for r in articles], [dict(r) for r in podcasts]


async def _fetch_since(conn, since: str | None) -> tuple[list[dict], list[dict]]:
    """Incremental: fetch only content newer than `since` (ISO date string)."""
    cutoff = date.fromisoformat(since[:10]) if since else (date.today() - timedelta(days=3))
    articles = await conn.fetch(
        f"""SELECT source, title, published_at,
                  LEFT(refined_content, {ARTICLE_TRUNC}) AS body
           FROM articles
           WHERE status='active' AND source NOT LIKE 'podcast_%'
             AND refined_content IS NOT NULL AND LENGTH(refined_content) > 50
             AND published_at::date > $1
           ORDER BY published_at DESC LIMIT 80""",
        cutoff,
    )
    podcasts = await conn.fetch(
        f"""SELECT source, title, published_at,
                  LEFT(refined_content, {PODCAST_TRUNC}) AS body
           FROM articles
           WHERE status='active' AND source LIKE 'podcast_%'
             AND refined_content IS NOT NULL AND LENGTH(refined_content) > 50
             AND published_at::date > $1
           ORDER BY published_at DESC""",
        cutoff,
    )
    return [dict(r) for r in articles], [dict(r) for r in podcasts]


def _build_content_block(articles: list[dict], podcasts: list[dict]) -> str:
    lines = []
    for r in articles:
        dt = str(r["published_at"])[:10]
        lines.append(f"[{dt}|{r['source']}] {r['title']}\n{r['body']}")
    if podcasts:
        lines.append("\n--- Podcast ---")
        for r in podcasts:
            dt = str(r["published_at"])[:10]
            lines.append(f"[{dt}|{r['source']}] {r['title']}\n{r['body']}")
    return "\n---\n".join(lines)


# ── Prompts ───────────────────────────────────────────────────────────────────

_REBUILD_PROMPT = """\
你是一位專精台美股市的資深投資分析師。以下是台灣主流投資分析平台及 Podcast 的精煉內容。

【任務】
建立一份「台美股投資主題字典」。

【顆粒度要求 — 非常重要】
必須「細粒度」：
✅ HBM記憶體、CoWoS先進封裝、光通訊模組800G、MLCC、石英晶體、PCB鑽針、液冷散熱、氣冷散熱、台積電條款（ETF持股上限）、ABF載板、伺服器VR電源、車用SiC、AI推論ASIC
❌ 半導體（太廣）、科技股（太廣）

【keyword 欄位說明 — 最關鍵】
keyword 必須是「單一字串」（非陣列），代表此主題最獨特、最無歧義的技術識別詞。
此詞會直接用來搜尋文章，出現即代表文章在討論此主題。
原則：越精準越好，寧可窄不可廣。
✅ "CoWoS"、"HBM"、"液冷散熱"、"ABF載板"、"鑽針"、"MLCC"、"石英晶體"
❌ "散熱"（液冷氣冷都中）、"記憶體"（太廣）、"AI"（到處都有）

【股票標的】
tw_stocks：{{"code":"2330","name":"台積電"}}，不限文章中出現者，請補全同族群重要台股
us_stocks：{{"ticker":"NVDA","name":"Nvidia"}}，同上

=== 內容 ===
{content}
=== 結束 ===

只輸出 JSON，不要任何說明：
{{
  "themes": [
    {{
      "id": "hbm_memory",
      "name": "HBM記憶體",
      "keyword": "HBM",
      "tw_stocks": [{{"code":"2408","name":"南亞科"}}],
      "us_stocks": [{{"ticker":"MU","name":"Micron"}}]
    }}
  ]
}}"""

_APPEND_PROMPT = """\
你是台美股投資分析師。以下是最新的投資分析內容（新增文章/Podcast）。

【現有主題列表】
{existing}

【任務】
分析新內容，判斷是否出現「現有主題清單中沒有的」新投資主題。
若有，輸出新主題（格式同下）；若無，只輸出：NO_NEW_THEMES

【keyword 規則】同樣必須是單一精準字串，避免和現有主題的 keyword 重複或過於相近。

=== 新內容 ===
{content}
=== 結束 ===

若有新主題，只輸出 JSON：
{{
  "themes": [
    {{
      "id": "new_theme_id",
      "name": "新主題名稱",
      "keyword": "精準識別詞",
      "tw_stocks": [],
      "us_stocks": []
    }}
  ]
}}
若無新主題，只輸出：NO_NEW_THEMES"""


# ── Public API ────────────────────────────────────────────────────────────────

async def rebuild_full(conn, api_key: str) -> int:
    """Full rebuild from all DB content. Returns theme count."""
    articles, podcasts = await _fetch_all(conn)
    print(f"  Full rebuild: {len(articles)} articles + {len(podcasts)} podcasts")
    content = _build_content_block(articles, podcasts)
    prompt  = _REBUILD_PROMPT.format(content=content)
    print(f"  Prompt: {len(prompt):,} chars (~{len(prompt)//3:,} tokens) → {GEMINI_MODEL}…")

    raw    = _call_gemini(api_key, prompt)
    themes = _parse_themes(raw)
    if not themes:
        print(f"  ❌ Could not parse themes. First 300 chars: {raw[:300]}")
        return 0

    data = {
        "meta": {
            "version": "1",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_checked": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": GEMINI_MODEL,
            "source_articles": len(articles),
            "source_podcasts": len(podcasts),
        },
        "themes": themes,
    }
    _save_dict(data)
    return len(themes)


async def append_new_themes(conn, api_key: str) -> int:
    """Incremental: check new content for emerging themes. Returns count added."""
    existing_data = _load_dict()
    existing      = existing_data.get("themes", [])
    last_checked  = existing_data.get("meta", {}).get("last_checked")

    articles, podcasts = await _fetch_since(conn, last_checked)
    if not articles and not podcasts:
        print("  [theme_dict] No new content since last check")
        _update_last_checked(existing_data)
        return 0

    print(f"  [theme_dict] {len(articles)} new articles + {len(podcasts)} new podcasts → checking for new themes…")

    existing_summary = "\n".join(
        f"- {t['name']} (keyword: \"{t.get('keyword','')}\")"
        for t in existing
    )
    content = _build_content_block(articles, podcasts)
    prompt  = _APPEND_PROMPT.format(existing=existing_summary, content=content)

    try:
        raw = _call_gemini(api_key, prompt)
    except Exception as e:
        print(f"  [theme_dict] Gemini error: {e}")
        return 0

    if "NO_NEW_THEMES" in raw.upper()[:50]:
        print("  [theme_dict] No new themes detected")
        _update_last_checked(existing_data)
        return 0

    new_themes = _parse_themes(raw)
    if not new_themes:
        print("  [theme_dict] Could not parse response, skipping")
        _update_last_checked(existing_data)
        return 0

    # Deduplicate by id
    existing_ids = {t["id"] for t in existing}
    added = [t for t in new_themes if t["id"] not in existing_ids]
    if not added:
        print("  [theme_dict] All returned themes already exist")
        _update_last_checked(existing_data)
        return 0

    existing_data["themes"].extend(added)
    _update_last_checked(existing_data)
    print(f"  [theme_dict] ✅ Added {len(added)} new theme(s): {[t['name'] for t in added]}")
    return len(added)


def _update_last_checked(data: dict) -> None:
    data.setdefault("meta", {})["last_checked"] = (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    _save_dict(data)


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("❌ GOOGLE_API_KEY not set")
        return

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        if "--rebuild" in sys.argv:
            n = await rebuild_full(conn, api_key)
            print(f"\n✅ {n} themes written to {DICT_FILE}")
        else:
            n = await append_new_themes(conn, api_key)
            if n:
                print(f"\n✅ Dictionary updated (+{n} themes)")
            else:
                print(f"\n✅ Dictionary up to date")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
