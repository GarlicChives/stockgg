#!/usr/bin/env python3
"""M3 Daily Report Generator — uses Google Gemini 2.0 Flash (free tier).

Pulls market data + top-30 rankings + recent articles from DB,
sends to Gemini for analysis, stores result and prints report.

Requires GOOGLE_API_KEY in .env.
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg
import urllib.request
from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models"


def _gemini_http(api_key: str, model: str, prompt: str,
                 temperature: float = 0.3, max_tokens: int = 3000) -> str:
    """Call Gemini REST API directly — avoids SDK version issues."""
    url = f"{GEMINI_BASE}/{model}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
REPORT_LOOKBACK_DAYS = 7
# Per article: prefer refined (~800 chars) over raw (truncated to 800 chars)
# Keeps total context lean — Gemini Flash free tier is 1M tokens/day but
# shorter prompts give faster responses
ARTICLE_CHARS = 800
MAX_ARTICLES  = 25


def _fmt_pct(v) -> str:
    if v is None:
        return "N/A"
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


async def _load_market_context(conn, report_date: date) -> dict:
    snap_date = await conn.fetchval("SELECT MAX(snapshot_date) FROM market_snapshots")
    if snap_date is None:
        return {}

    snaps = await conn.fetch(
        "SELECT market, symbol, close_price, change_pct, extra "
        "FROM market_snapshots WHERE snapshot_date=$1",
        snap_date,
    )
    indicators = {}
    for s in snaps:
        name = (json.loads(s["extra"] or "{}")).get("name", s["symbol"])
        indicators[s["symbol"]] = {
            "name": name,
            "close": float(s["close_price"] or 0),
            "change_pct": float(s["change_pct"]) if s["change_pct"] is not None else None,
        }

    rank_date = await conn.fetchval("SELECT MAX(rank_date) FROM trading_rankings")
    us_ranks, tw_ranks = [], []
    if rank_date:
        for row in await conn.fetch(
            "SELECT rank, ticker, name, trading_value, change_pct, is_limit_up_30m "
            "FROM trading_rankings WHERE rank_date=$1 AND market='US' ORDER BY rank",
            rank_date,
        ):
            us_ranks.append({
                "rank": row["rank"], "ticker": row["ticker"], "name": row["name"],
                "value_b": round(float(row["trading_value"] or 0) / 1e9, 1),
                "change_pct": float(row["change_pct"]) if row["change_pct"] is not None else None,
            })
        for row in await conn.fetch(
            "SELECT rank, ticker, name, trading_value, change_pct, is_limit_up_30m "
            "FROM trading_rankings WHERE rank_date=$1 AND market='TW' ORDER BY rank",
            rank_date,
        ):
            tw_ranks.append({
                "rank": row["rank"], "ticker": row["ticker"], "name": row["name"],
                "value_b": round(float(row["trading_value"] or 0) / 1e8, 1),
                "change_pct": float(row["change_pct"]) if row["change_pct"] is not None else None,
                "limit_up": bool(row["is_limit_up_30m"]),
            })

    return {
        "snap_date": str(snap_date),
        "rank_date": str(rank_date) if rank_date else None,
        "indicators": indicators,
        "us_top30": us_ranks,
        "tw_top30": tw_ranks,
    }


async def _load_article_context(conn, lookback_days: int) -> str:
    """Prefer refined_content (already filtered), fall back to raw truncated.
    Limits articles and chars per article to keep prompt size low."""
    cutoff = date.today() - timedelta(days=lookback_days)
    rows = await conn.fetch(
        f"""SELECT source, title, published_at,
                  COALESCE(LEFT(refined_content, {ARTICLE_CHARS}),
                           LEFT(content, {ARTICLE_CHARS})) AS body
           FROM articles
           WHERE published_at >= $1 AND status='active'
             AND content IS NOT NULL
           ORDER BY published_at DESC
           LIMIT {MAX_ARTICLES}""",
        cutoff,
    )
    parts = []
    for r in rows:
        date_str = str(r["published_at"])[:10] if r["published_at"] else "?"
        parts.append(f"[{date_str}|{r['source']}] {r['title']}\n{r['body'] or ''}")
    return "\n---\n".join(parts)


def _build_prompt(market: dict, articles: str) -> str:
    ind = market.get("indicators", {})

    def get(sym):
        d = ind.get(sym, {})
        return d.get("close", 0), d.get("change_pct")

    sp500_c,  sp500_p  = get("^GSPC")
    nasdaq_c, nasdaq_p = get("^IXIC")
    sox_c,    sox_p    = get("^SOX")
    nikkei_c, nikkei_p = get("^N225")
    taiex_c,  taiex_p  = get("^TWII")
    vix_c,    _        = get("^VIX")
    yield10_c, _       = get("^TNX")
    dxy_c,    dxy_p    = get("DX-Y.NYB")
    fg_c,     _        = get("FEAR_GREED")

    us_lines = "\n".join(
        f"#{r['rank']:2d} {r['ticker']:6s} {r['name'][:20]:20s} ${r['value_b']:.1f}B {_fmt_pct(r['change_pct'])}"
        for r in market.get("us_top30", [])
    )
    tw_lines = "\n".join(
        f"#{r['rank']:2d} {r['ticker']} {r['name'][:8]:8s} {r['value_b']:.0f}億 {_fmt_pct(r['change_pct'])}"
        + (" 漲停" if r.get("limit_up") else "")
        for r in market.get("tw_top30", [])
    )

    return f"""你是資深投資研究員，根據市場數據與近期文章，用繁體中文產出每日投資簡報。

=== 市場數據 {market.get('snap_date','')} ===
美股 S&P500={sp500_c:.0f}({_fmt_pct(sp500_p)}) NASDAQ={nasdaq_c:.0f}({_fmt_pct(nasdaq_p)}) SOX={sox_c:.0f}({_fmt_pct(sox_p)})
日股 N225={nikkei_c:.0f}({_fmt_pct(nikkei_p)}) 台股 TWII={taiex_c:.0f}({_fmt_pct(taiex_p)})
VIX={vix_c:.1f} 10Y={yield10_c:.2f}% DXY={dxy_c:.2f}({_fmt_pct(dxy_p)}) 恐慌貪婪={fg_c:.0f}

=== 成交值排行 {market.get('rank_date','')} ===
US前30:
{us_lines or '(無資料)'}
TW前30:
{tw_lines or '(無資料)'}

=== 近期研究文章 ===
{articles}

=== 輸出格式 ===
## 總經近況
（100字內）

## 國際股市
（條列各指數漲跌+驅動因素）

## 綜合多空判斷
- 短期(1-2週)：[偏多/中立/偏空] — 理由
- 中期(1-3月)：[偏多/中立/偏空] — 理由
- 長期(3-12月)：[偏多/中立/偏空] — 理由
- 關鍵風險：

## 今日焦點股分析
（依成交值排行分析市場正在反應的題材）
### 題材一：[名稱]（信心度：高/中/低）
- 核心標的：
- 市場在反應：
- 供應鏈機會：

### 題材二（如有）：
### 題材三（如有）：

## 明日觀察重點
（2-3個具體事件或指標）
"""


async def generate_report(report_date: date | None = None) -> str:
    if report_date is None:
        report_date = date.today()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "❌ 未設定 GOOGLE_API_KEY，請在 .env 加入後重試。"


    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    market   = await _load_market_context(conn, report_date)
    articles = await _load_article_context(conn, REPORT_LOOKBACK_DAYS)
    await conn.close()

    prompt = _build_prompt(market, articles)
    print(f"  Prompt size: {len(prompt)} chars → sending to {GEMINI_MODEL}…")

    try:
        report_text = _gemini_http(api_key, GEMINI_MODEL, prompt)
    except Exception as e:
        return f"❌ Gemini 錯誤：{e}"

    # Persist to DB
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute(
        """INSERT INTO analysis_reports
           (report_date, macro_summary, market_summary, raw_prompt, raw_response)
           VALUES ($1,$2,$3,$4,$5)
           ON CONFLICT (report_date) DO UPDATE
           SET macro_summary=$2, market_summary=$3,
               raw_prompt=$4, raw_response=$5""",
        report_date,
        report_text[:500],
        json.dumps(market.get("indicators", {})),
        prompt[:4000],
        report_text,
    )
    await conn.close()

    return report_text


if __name__ == "__main__":
    report = asyncio.run(generate_report())
    print(report)
