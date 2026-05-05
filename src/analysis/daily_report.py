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

from src.utils import db
import urllib.request
from dotenv import load_dotenv

load_dotenv()

from src.prompts import render as render_prompt
from src.utils.api_logger import log_usage

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE  = "https://generativelanguage.googleapis.com/v1beta/models"


def _gemini_http(api_key: str, model: str, prompt: str,
                 temperature: float = 0.3, max_tokens: int = 8192) -> str:
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
    usage = data.get("usageMetadata", {})
    log_usage(
        "gemini", model, "daily_report",
        usage.get("promptTokenCount", 0),
        usage.get("candidatesTokenCount", 0),
        usage.get("thoughtsTokenCount", 0),
    )
    # Gemini 2.5 Flash is a thinking model — parts may include thought traces
    # (thought=True). Collect only the actual response parts.
    parts = data["candidates"][0]["content"]["parts"]
    text = "".join(
        p["text"] for p in parts if "text" in p and not p.get("thought", False)
    ).strip()
    return text or "".join(p.get("text", "") for p in parts).strip()
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
    # Use DISTINCT ON so each symbol returns its own latest non-null date.
    # This prevents symbols updated on different days from being excluded.
    snaps = await conn.fetch("""
        SELECT DISTINCT ON (symbol)
            symbol, close_price, change_pct, snapshot_date, extra
        FROM market_snapshots
        WHERE close_price IS NOT NULL
        ORDER BY symbol, snapshot_date DESC
    """)
    if not snaps:
        return {}

    snap_date = max(r["snapshot_date"] for r in snaps)
    indicators = {}
    for s in snaps:
        extra = s["extra"] if isinstance(s["extra"], dict) else json.loads(s["extra"] or "{}")
        name = extra.get("name", s["symbol"])
        indicators[s["symbol"]] = {
            "name": name,
            "close": float(s["close_price"]),
            "change_pct": float(s["change_pct"]) if s["change_pct"] is not None else None,
        }

    rank_date = await conn.fetchval("SELECT MAX(rank_date) FROM trading_rankings")
    us_ranks, tw_ranks = [], []
    if rank_date:
        for row in await conn.fetch(
            """SELECT ROW_NUMBER() OVER (ORDER BY trading_value DESC NULLS LAST)::int AS rank,
                      ticker, name, trading_value, change_pct, is_limit_up_30m
               FROM trading_rankings WHERE rank_date=$1 AND market='US'
               ORDER BY trading_value DESC NULLS LAST LIMIT 30""",
            rank_date,
        ):
            us_ranks.append({
                "rank": row["rank"], "ticker": row["ticker"], "name": row["name"],
                "value_b": round(float(row["trading_value"] or 0) / 1e9, 1),
                "change_pct": float(row["change_pct"]) if row["change_pct"] is not None else None,
            })
        for row in await conn.fetch(
            """SELECT ROW_NUMBER() OVER (ORDER BY trading_value DESC NULLS LAST)::int AS rank,
                      ticker, name, trading_value, change_pct, is_limit_up_30m
               FROM trading_rankings WHERE rank_date=$1 AND market='TW'
               ORDER BY trading_value DESC NULLS LAST LIMIT 30""",
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
    topix_c,  topix_p  = get("1308.T")
    kospi_c,  kospi_p  = get("^KS11")
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

    return render_prompt(
        "daily_report",
        snap_date=market.get("snap_date", ""),
        rank_date=market.get("rank_date", ""),
        sp500=f"{sp500_c:.0f}",   sp500_pct=_fmt_pct(sp500_p),
        nasdaq=f"{nasdaq_c:.0f}", nasdaq_pct=_fmt_pct(nasdaq_p),
        sox=f"{sox_c:.0f}",       sox_pct=_fmt_pct(sox_p),
        topix=f"{topix_c:.0f}",   topix_pct=_fmt_pct(topix_p),
        kospi=f"{kospi_c:.0f}",   kospi_pct=_fmt_pct(kospi_p),
        taiex=f"{taiex_c:.0f}",   taiex_pct=_fmt_pct(taiex_p),
        vix=f"{vix_c:.1f}",
        yield10=f"{yield10_c:.2f}",
        dxy=f"{dxy_c:.2f}",       dxy_pct=_fmt_pct(dxy_p),
        fg=f"{fg_c:.0f}",
        us_lines=us_lines or "(無資料)",
        tw_lines=tw_lines or "(無資料)",
        articles=articles,
    )


async def generate_report(report_date: date | None = None) -> str:
    if report_date is None:
        report_date = date.today()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "❌ 未設定 GOOGLE_API_KEY，請在 .env 加入後重試。"


    conn = await db.connect()
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
    conn = await db.connect()
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
