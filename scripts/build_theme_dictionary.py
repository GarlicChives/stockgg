#!/usr/bin/env python3
"""Theme dictionary maintenance via Search + LLM classification.

Flow per uncached stock (TTL = 30 days):
  1. Fetch TW+US top-30 from DB
  2. Skip tickers fresh in cache (< 30 days)
  3. Search → collect snippets
  4. LLM classify → {matched: [...], new_themes: [{id,name,keyword}, ...]}
  5. Dedup new_themes against existing dict (LLM may miss near-matches)
  6. Auto-create truly-new themes (flagged auto_created=True)
  7. Upsert stock into all matched themes
  8. Update cache + persist

Provider swap: pass search_provider= / classifier_provider= to run().
Requires env vars:
  GOOGLE_API_KEY  — Gemini (classifier)
  TAVILY_API_KEY  — Tavily search
"""
import asyncio
import json
import re
import sys
from datetime import date
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
from src.theme.search import TavilyProvider, SearchProvider, build_query
from src.theme.classifier import GeminiClassifier, ClassifierProvider

DICT_FILE = Path(__file__).resolve().parents[1] / "data" / "theme_dictionary.json"
LOCK_FILE = DICT_FILE.with_suffix(".lock")


# ── File lock (prevent parallel runs from corrupting the dict) ────────────────

class _DictLock:
    """Best-effort cross-process lock via O_EXCL on a sidecar file.

    If another build is running we ABORT — better to skip than to race-write.
    Stale locks are auto-cleared after 30 minutes.
    """
    def __init__(self):
        self.acquired = False

    def __enter__(self):
        import time
        if LOCK_FILE.exists():
            age = time.time() - LOCK_FILE.stat().st_mtime
            if age < 1800:
                raise RuntimeError(
                    f"theme_dictionary already being built (lock {age:.0f}s old). "
                    f"Remove {LOCK_FILE} to force.")
            LOCK_FILE.unlink()  # stale
        LOCK_FILE.write_text(str(__import__('os').getpid()))
        self.acquired = True
        return self

    def __exit__(self, *exc):
        if self.acquired and LOCK_FILE.exists():
            LOCK_FILE.unlink()


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


# ── Auto-discovery dedup + creation ───────────────────────────────────────────

_NAME_NOISE_RE = re.compile(r"[概念股的之、，,。.\s]")


def _normalize_theme_name(s: str) -> str:
    """Strip noise so '機器人概念股' / '機器人' / '智慧機器人' match-fuzzy."""
    return _NAME_NOISE_RE.sub("", (s or "").lower())


def _find_existing_theme(name: str, keyword: str, themes: list[dict]) -> str | None:
    """Match a proposed new theme against the dict by normalized name/keyword.
    Returns the existing theme ID if a near-match found, else None."""
    n = _normalize_theme_name(name)
    k = _normalize_theme_name(keyword)
    if not n and not k:
        return None
    for t in themes:
        existing_n = _normalize_theme_name(t.get("name", ""))
        existing_k = _normalize_theme_name(t.get("keyword", ""))
        if n and (n == existing_n or n == existing_k):
            return t["id"]
        if k and (k == existing_n or k == existing_k):
            return t["id"]
    return None


def _create_theme(themes_data: dict, proposal: dict) -> str:
    """Append a new theme to the dict. Resolves ID collisions with _2, _3 suffixes.
    Returns the final ID written."""
    existing_ids = {t["id"] for t in themes_data["themes"]}
    base_id = proposal["id"]
    new_id  = base_id
    n = 2
    while new_id in existing_ids:
        new_id = f"{base_id}_{n}"
        n += 1
    themes_data["themes"].append({
        "id":            new_id,
        "name":          proposal["name"],
        "keyword":       proposal.get("keyword", proposal["name"]),
        "supply_chain":  {"upstream": [], "downstream": []},
        "tw_stocks":     [],
        "us_stocks":     [],
        "auto_created":  True,
        "auto_created_at": str(date.today()),
    })
    return new_id


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

    Returns stats dict: checked / skipped / searched / themes_created / inserted.
    Aborts (returns empty stats) if another build is already running.
    """
    try:
        lock_ctx = _DictLock().__enter__()
    except RuntimeError as e:
        if verbose:
            print(f"  [theme_dict] ⚠ {e}")
        return {"checked": 0, "skipped": 0, "searched": 0,
                "themes_created": 0, "inserted": 0}

    try:
        return await _run_inner(search_provider, classifier_provider, verbose)
    finally:
        lock_ctx.__exit__(None, None, None)


async def _run_inner(
    search_provider:     SearchProvider     | None,
    classifier_provider: ClassifierProvider | None,
    verbose: bool,
) -> dict:
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
    stats = {"checked": len(all_stocks), "skipped": 0, "searched": 0,
             "inserted": 0, "themes_created": 0}

    search_ok = search.available()
    clf_ok    = clf.available()
    if not search_ok and verbose:
        print("  [theme_dict] ⚠ Search provider not available (TAVILY_API_KEY not set)")
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
        result = clf.classify(snippets_text, themes_meta)
        matched_ids = list(result.get("matched", []))

        # Auto-discovery: dedup against existing dict; create if truly new
        for proposal in result.get("new_themes", []):
            existing_id = _find_existing_theme(
                proposal["name"], proposal.get("keyword", ""),
                themes_data["themes"],
            )
            if existing_id:
                if existing_id not in matched_ids:
                    matched_ids.append(existing_id)
                if verbose:
                    print(f"           ↳ '{proposal['name']}' 對到既有 → {existing_id}")
                continue
            new_id = _create_theme(themes_data, proposal)
            themes_meta.append({
                "id": new_id, "name": proposal["name"],
                "keyword": proposal.get("keyword", proposal["name"]),
            })
            matched_ids.append(new_id)
            stats["themes_created"] += 1
            dict_updated = True
            if verbose:
                print(f"           ➕ 新主題建立: {new_id} ({proposal['name']})")

        if verbose:
            label = ", ".join(matched_ids) if matched_ids else "—"
            print(f"           → matched: {label}")

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
            result = clf.classify("\n---\n".join(snippets), themes_meta)
            print(f"  Matched (existing): {result.get('matched', [])}")
            new_themes = result.get("new_themes", [])
            if new_themes:
                print(f"  New themes proposed:")
                for t in new_themes:
                    existing = _find_existing_theme(
                        t["name"], t.get("keyword", ""), themes_data["themes"])
                    flag = f"→ 對到 {existing}" if existing else "★ 真新主題"
                    print(f"    · {t['id']:30s} {t['name']:10s} {flag}")
        return

    stats = await run()
    print(
        f"[theme_dict] Done — "
        f"checked={stats['checked']}, "
        f"skipped={stats['skipped']}, "
        f"searched={stats['searched']}, "
        f"themes_created={stats['themes_created']}, "
        f"inserted={stats['inserted']}"
    )
    if stats["themes_created"] or stats["inserted"]:
        print(f"[theme_dict] ✅ 字典更新：新主題 +{stats['themes_created']}，"
              f"股票歸類 +{stats['inserted']}")
    else:
        print("[theme_dict] ✅ Dictionary up to date")


if __name__ == "__main__":
    asyncio.run(main())
