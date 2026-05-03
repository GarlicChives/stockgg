"""Focus stock theme detection.

Loads theme_dictionary.json, scores today's top-30 stocks against themes.

Gate logic (two conditions must both be true for a stock to be FOCAL):
  1. Dictionary membership: the stock must appear in the theme's tw_stocks/us_stocks.
     This prevents cross-sector pollution — an article mentioning DRAM and IP design
     does NOT place a DRAM company into the IP design cluster.
  2. Keyword confirmation: the stock's recent articles must contain the theme keyword,
     weighted by recency (≤7 days ×2, 8-60 days ×1).

DB migration path: replace _load_themes() body only — all callers unchanged.
"""
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

DICT_FILE    = Path(__file__).resolve().parents[2] / "data" / "theme_dictionary.json"
PRIMARY_DAYS = 7
MIN_SCORE    = 1.0   # minimum weighted keyword occurrence count
MIN_FOCAL    = 1     # minimum focal stocks for a cluster to appear


# ── Data classes (DB-migration-ready field naming) ────────────────────────────

@dataclass
class FocalStock:
    ticker: str
    name: str
    market: str           # "TW" | "US"
    change_pct: float | None
    trading_value: float
    rank: int
    limit_up: bool
    articles: list[dict]
    score: float          # weighted keyword score
    primary_keyword_hits: int  # primary-window articles with keyword match


@dataclass
class WatchStock:
    code_or_ticker: str   # maps to theme_dict.tw_stocks.code / us_stocks.ticker
    name: str
    market: str           # "TW" | "US"


@dataclass
class ThemeCluster:
    theme_id: str
    name: str
    focal: list[FocalStock]
    watch: list[WatchStock]
    total_score: float
    primary_art_count: int   # sum of primary keyword hits across focal stocks


# ── Dictionary loader (swap body for DB migration) ────────────────────────────

def _load_themes() -> list[dict]:
    """Return list of theme dicts from JSON file.

    DB migration: replace this body with:
        rows = asyncio.run(conn.fetch("SELECT * FROM theme_dictionary"))
        return [dict(r) for r in rows]
    """
    if not DICT_FILE.exists():
        return []
    with DICT_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("themes", [])


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_articles(articles: list[dict], keyword: str) -> tuple[float, int]:
    """
    Count occurrences of a single precise keyword across article list.
    Primary window (≤7 days) × weight 2; secondary (8-60 days) × weight 1.
    Returns (weighted_occurrence_total, primary_article_count_with_match).
    """
    cutoff_primary = date.today() - timedelta(days=PRIMARY_DAYS)
    kw = keyword.lower()
    total_score = 0.0
    primary_count = 0

    for art in articles:
        pub = art.get("published_at")
        if pub is None:
            continue
        art_date = pub.date() if hasattr(pub, "date") else pub
        is_primary = art_date >= cutoff_primary
        weight = 2.0 if is_primary else 1.0

        text = (
            (art.get("full_content") or "")
            + " "
            + (art.get("title") or "")
        ).lower()
        count = text.count(kw)
        if count:
            total_score += count * weight
            if is_primary:
                primary_count += 1

    return total_score, primary_count


# ── Main entry point ──────────────────────────────────────────────────────────

def detect_clusters(
    stocks: dict[str, dict],        # ticker → {name, market, rank, change_pct, trading_value, ...}
    ticker_arts: dict[str, list],   # ticker → list of article dicts (needs full_content, published_at)
) -> list[ThemeCluster]:
    """
    Match top-30 stocks to theme clusters using dictionary membership + keyword scoring.

    A stock is FOCAL in a theme iff:
      (a) it appears in the theme's tw_stocks (by code) or us_stocks (by ticker), AND
      (b) its recent articles contain the theme keyword with score ≥ MIN_SCORE.

    Returns clusters sorted by signal strength (primary_art_count DESC, focal count DESC, score DESC).
    Empty list if dictionary not built yet.
    """
    themes = _load_themes()
    if not themes:
        return []

    all_focal_codes = set(stocks.keys())

    clusters: list[ThemeCluster] = []
    for theme in themes:
        keyword = theme.get("keyword", "")
        if not keyword:
            continue

        # Gate 1: build the set of this theme's known member stocks
        dict_tw = {s["code"] for s in theme.get("tw_stocks", [])}
        dict_us = {s["ticker"] for s in theme.get("us_stocks", [])}
        dict_members = dict_tw | dict_us

        # Candidates = theme members that appear in today's top-30
        candidates = [t for t in all_focal_codes if t in dict_members]
        if not candidates:
            continue

        # Gate 2: keyword confirmation — candidate must have articles mentioning keyword
        focal_stocks: list[FocalStock] = []
        for ticker in candidates:
            arts = ticker_arts.get(ticker, [])
            score, primary_hits = _score_articles(arts, keyword)
            if score < MIN_SCORE:
                continue
            info = stocks[ticker]
            focal_stocks.append(FocalStock(
                ticker=ticker,
                name=info.get("name", ticker),
                market=info.get("market", ""),
                change_pct=info.get("change_pct"),
                trading_value=info.get("trading_value", 0),
                rank=info.get("rank", 99),
                limit_up=bool(info.get("limit_up", False)),
                articles=arts,
                score=score,
                primary_keyword_hits=primary_hits,
            ))

        if len(focal_stocks) < MIN_FOCAL:
            continue

        focal_stocks.sort(key=lambda s: -s.score)

        # Watch stocks: theme dictionary members NOT in today's top-30
        tw_watch = [
            WatchStock(s["code"], s["name"], "TW")
            for s in theme.get("tw_stocks", [])
            if s["code"] not in all_focal_codes
        ]
        us_watch = [
            WatchStock(s["ticker"], s["name"], "US")
            for s in theme.get("us_stocks", [])
            if s["ticker"] not in all_focal_codes
        ]

        total_score = sum(s.score for s in focal_stocks)
        primary_art_count = sum(s.primary_keyword_hits for s in focal_stocks)

        clusters.append(ThemeCluster(
            theme_id=theme["id"],
            name=theme["name"],
            focal=focal_stocks,
            watch=(tw_watch[:6] + us_watch[:4]),
            total_score=total_score,
            primary_art_count=primary_art_count,
        ))

    return sorted(
        clusters,
        key=lambda c: (-c.primary_art_count, -len(c.focal), -c.total_score),
    )
