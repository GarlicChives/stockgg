#!/usr/bin/env python3
"""Theme dictionary maintenance via Search + LLM classification.

Flow per uncached stock (TTL = 30 days):
  1. Fetch TW+US top-30 from DB
  2. Skip tickers fresh in cache (< 30 days)
  3. Search → collect snippets
  4. LLM classify → list of matching theme IDs
  5. Upsert stock into theme_dictionary.json
  6. Update cache + persist

Provider swap: pass search_provider= / classifier_provider= to run().
Requires env vars:
  GOOGLE_API_KEY     — Gemini (classifier)
  GOOGLE_CSE_API_KEY — Google Custom Search
  GOOGLE_CSE_CX      — Custom Search Engine ID
"""
import asyncio
import json
import re
import sys
from pathlib import Path

_ETF_TW_RE = re.compile(r'^00\d')

def _is_etf(ticker: str, name: str = "") -> bool:
    if _ETF_TW_RE.match(ticker):
        return True
    return "ETF" in (name or "").upper()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from src.utils import db
from src.theme.cache import CacheManager
from src.theme.search import GoogleCSEProvider, TavilyProvider, SearchProvider, build_query
from src.theme.classifier import GeminiClassifier, ClassifierProvider

DICT_FILE = Path(__file__).resolve().parents[1] / "data" / "theme_dictionary.json"


# ── Dictionary I/O ────────────────────────────────────────────────────────────

def _load_dict() -> dict:
    if not DICT_FILE.exists():
        return {"themes": []}
    return json.loads(DICT_FILE.read_text(encoding="utf-8"))


def _save_dict(data: dict) -> None:
    DICT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _upsert_stock(
    themes_data: dict,
    theme_id: str,
    ticker: str,
    name: str,
    market: str,
) -> bool:
    """Insert stock into theme if not already present. Returns True if newly added."""
    for theme in themes_data["themes"]:
        if theme["id"] != theme_id:
            continue
        key   = "tw_stocks" if market == "TW" else "us_stocks"
        field = "code"      if market == "TW" else "ticker"
        existing = {s[field] for s in theme.get(key, [])}
        if ticker not in existing:
            theme.setdefault(key, []).append({field: ticker, "name": name})
            return True
        return False
    return False  # theme_id not found in dictionary


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _fetch_top30(conn, market: str) -> list[dict]:
    rank_date = await conn.fetchval(
        "SELECT MAX(rank_date) FROM trading_rankings WHERE market=$1", market
    )
    if not rank_date:
        return []
    rows = await conn.fetch(
        "SELECT ticker, name FROM trading_rankings "
        "WHERE rank_date=$1 AND market=$2 AND rank <= 30 ORDER BY rank",
        rank_date, market,
    )
    return [
        {"ticker": r["ticker"], "name": r["name"] or r["ticker"], "market": market}
        for r in rows
    ]


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run(
    search_provider:     SearchProvider     | None = None,
    classifier_provider: ClassifierProvider | None = None,
    verbose: bool = True,
) -> dict:
    """
    Classify today's top-30 TW+US stocks and update theme_dictionary.json.

    Returns stats dict: checked / skipped / searched / inserted.
    """
    search = search_provider     or TavilyProvider()
    clf    = classifier_provider or GeminiClassifier()
    cache  = CacheManager()

    themes_data = _load_dict()
    # Build compact theme list for classifier prompt (id + keyword + name)
    themes_meta = [
        {"id": t["id"], "name": t["name"], "keyword": t.get("keyword", "")}
        for t in themes_data["themes"]
        if t.get("keyword")
    ]

    conn = await db.connect()
    tw_stocks = await _fetch_top30(conn, "TW")
    us_stocks = await _fetch_top30(conn, "US")
    await conn.close()

    all_stocks = tw_stocks + us_stocks
    stats = {"checked": len(all_stocks), "skipped": 0, "searched": 0, "inserted": 0}

    search_ok = search.available()
    clf_ok    = clf.available()
    if not search_ok and verbose:
        print("  [theme_dict] ⚠ Search provider not available (GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX not set)")
    if not clf_ok and verbose:
        print("  [theme_dict] ⚠ Classifier not available (GOOGLE_API_KEY not set)")
    if not search_ok or not clf_ok:
        return stats

    dict_updated = False

    for stock in all_stocks:
        ticker = stock["ticker"]
        name   = stock["name"]
        market = stock["market"]

        if _is_etf(ticker, name):
            stats["skipped"] += 1
            if verbose:
                print(f"  [skip]   {ticker:8s} {name[:16]:16s} (ETF)")
            continue

        if cache.is_fresh(ticker):
            stats["skipped"] += 1
            if verbose:
                print(f"  [skip]   {ticker:8s} {name[:16]:16s} (cached)")
            continue

        query    = build_query(name, ticker, market)
        snippets = search.search(query)
        stats["searched"] += 1

        if verbose:
            print(f"  [search] {ticker:8s} {name[:16]:16s} → {len(snippets)} snippets")

        if not snippets:
            cache.set(ticker, name, market, [])
            continue

        snippets_text = "\n---\n".join(snippets)
        matched_ids   = clf.classify(snippets_text, themes_meta)

        if verbose:
            label = ", ".join(matched_ids) if matched_ids else "—"
            print(f"           → {label}")

        inserted_now = []
        for theme_id in matched_ids:
            if _upsert_stock(themes_data, theme_id, ticker, name, market):
                stats["inserted"] += 1
                inserted_now.append(theme_id)
                dict_updated = True

        if verbose and inserted_now:
            print(f"           ✅ inserted into: {inserted_now}")

        cache.set(ticker, name, market, matched_ids)

    if dict_updated:
        _save_dict(themes_data)
    cache.save()

    return stats


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build theme dictionary via Search+LLM")
    parser.add_argument("--reset-cache", action="store_true",
                        help="Clear classification cache — forces re-search for all tickers")
    parser.add_argument("--ticker",      help="Classify a single ticker (bypasses TTL)")
    args = parser.parse_args()

    print("[theme_dict] Search+LLM theme classification...")

    if args.reset_cache:
        c = CacheManager()
        c.reset()
        c.save()
        print("[theme_dict] Cache cleared")

    if args.ticker:
        # Single-ticker debug mode
        ticker = args.ticker.upper()
        search = TavilyProvider()
        clf    = GeminiClassifier()
        themes_data = _load_dict()
        themes_meta = [
            {"id": t["id"], "name": t["name"], "keyword": t.get("keyword", "")}
            for t in themes_data["themes"] if t.get("keyword")
        ]
        market = "TW" if ticker.isdigit() else "US"

        # Try to resolve real company name from DB
        try:
            conn = await db.connect()
            row  = await conn.fetchrow(
                "SELECT name FROM trading_rankings WHERE ticker=$1 AND market=$2 "
                "ORDER BY rank_date DESC LIMIT 1",
                ticker, market,
            )
            await conn.close()
            name = row["name"] if row and row["name"] else ticker
        except Exception:
            name = ticker
        query = build_query(name, ticker, market)
        print(f"  Query: {query}")
        snippets = search.search(query)
        print(f"  Snippets ({len(snippets)}):")
        for s in snippets:
            print(f"    · {s[:120]}")
        if snippets:
            matched = clf.classify("\n---\n".join(snippets), themes_meta)
            print(f"  Matched: {matched}")
        return

    stats = await run()
    print(
        f"[theme_dict] Done — "
        f"checked={stats['checked']}, "
        f"skipped={stats['skipped']}, "
        f"searched={stats['searched']}, "
        f"inserted={stats['inserted']}"
    )
    if stats["inserted"]:
        print(f"[theme_dict] ✅ Dictionary updated (+{stats['inserted']} stock entries)")
    else:
        print("[theme_dict] ✅ Dictionary up to date")


if __name__ == "__main__":
    asyncio.run(main())
