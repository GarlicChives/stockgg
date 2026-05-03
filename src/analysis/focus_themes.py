"""Focus stock theme detection.

Loads theme_dictionary.json, scores today's top-30 stocks against themes
using a dual-window approach:
  - Primary window (≤7 days):  article keyword match × weight 2
  - Secondary window (8-60 days): keyword match × weight 1

DB migration path: replace _load_themes() body only — all callers unchanged.
"""
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

DICT_FILE    = Path(__file__).resolve().parents[2] / "data" / "theme_dictionary.json"
PRIMARY_DAYS = 7
MIN_SCORE    = 1.5   # minimum weighted keyword score to link a stock to a theme
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
    primary_art_count: int   # supporting articles in the 7-day window


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

def _score_articles(articles: list[dict], keywords: list[str]) -> tuple[float, int]:
    """
    Weighted keyword match across article list.
    Returns (total_weighted_score, primary_article_count).
    """
    cutoff_primary = date.today() - timedelta(days=PRIMARY_DAYS)
    kw_lower = [k.lower() for k in keywords]
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
        matches = sum(1 for kw in kw_lower if kw in text)
        if matches:
            total_score += matches * weight
            if is_primary:
                primary_count += 1

    return total_score, primary_count


# ── Main entry point ──────────────────────────────────────────────────────────

def detect_clusters(
    stocks: dict[str, dict],        # ticker → {name, market, rank, change_pct, trading_value, ...}
    ticker_arts: dict[str, list],   # ticker → list of article dicts (needs full_content, published_at)
) -> list[ThemeCluster]:
    """
    Match top-30 stocks to theme clusters using keyword scoring.
    Returns clusters sorted by signal strength (primary articles DESC, focal count DESC, score DESC).
    Empty list if dictionary not built yet.
    """
    themes = _load_themes()
    if not themes:
        return []

    focal_tickers = {t for t in ticker_arts if ticker_arts[t]}
    all_focal_codes = set(stocks.keys())

    # Score every focal ticker against every theme
    ticker_scores: dict[str, dict[str, tuple]] = {}   # ticker → {theme_id: (score, primary_count)}
    for ticker in focal_tickers:
        ticker_scores[ticker] = {}
        for theme in themes:
            score, primary = _score_articles(ticker_arts[ticker], theme["keywords"])
            if score >= MIN_SCORE:
                ticker_scores[ticker][theme["id"]] = (score, primary)

    # Build clusters
    clusters: dict[str, ThemeCluster] = {}
    for theme in themes:
        tid = theme["id"]
        focal_stocks: list[FocalStock] = []

        for ticker, scored_themes in ticker_scores.items():
            if tid not in scored_themes:
                continue
            score, _ = scored_themes[tid]
            info = stocks[ticker]
            focal_stocks.append(FocalStock(
                ticker=ticker,
                name=info.get("name", ticker),
                market=info.get("market", ""),
                change_pct=info.get("change_pct"),
                trading_value=info.get("trading_value", 0),
                rank=info.get("rank", 99),
                limit_up=bool(info.get("limit_up", False)),
                articles=ticker_arts[ticker],
                score=score,
            ))

        if len(focal_stocks) < MIN_FOCAL:
            continue

        focal_stocks.sort(key=lambda s: -s.score)

        # Watch stocks: from dictionary, excluding today's top-30 tickers
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
        primary_art_count = sum(
            1 for s in focal_stocks for art in s.articles
            if art.get("published_at") and
            (art["published_at"].date() if hasattr(art["published_at"], "date") else art["published_at"])
            >= date.today() - timedelta(days=PRIMARY_DAYS)
        )

        clusters[tid] = ThemeCluster(
            theme_id=tid,
            name=theme["name"],
            focal=focal_stocks,
            watch=(tw_watch[:6] + us_watch[:4]),
            total_score=total_score,
            primary_art_count=primary_art_count,
        )

    return sorted(
        clusters.values(),
        key=lambda c: (-c.primary_art_count, -len(c.focal), -c.total_score),
    )
