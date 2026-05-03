"""Focus stock theme detection.

Loads theme_dictionary.json, scores today's top-30 stocks against themes.

Two pathways for a cluster to surface (Gate 1 always required):

  Gate 1 — Dictionary membership (mandatory):
    The stock must appear in the theme's tw_stocks/us_stocks.
    Prevents cross-sector pollution (DRAM article ≠ IP design cluster).

  Gate 2a — Keyword discussion (primary):
    Stock's recent articles contain the theme keyword (weighted by recency).
    Indicates the theme is actively being written about for this stock.

  Gate 2b — Volume rotation (secondary, requires ≥2 members):
    ≥2 dictionary members simultaneously in top-30 by trading value.
    Pure price/volume signal; no article confirmation needed.
    Displayed with a distinct "量能輪動" badge.

Cluster is included if Gate 1 AND (Gate 2a OR Gate 2b).

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
MIN_VOLUME   = 2     # minimum top-30 members to trigger volume-rotation pathway


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
    score: float          # weighted keyword score (0 for volume-only)
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
    volume_only: bool = False  # True when surfaced via volume-rotation pathway (no keyword confirm)


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
    Match top-30 stocks to theme clusters.
    Gate 1 (dictionary membership) is always required.
    Gate 2a (keyword) or Gate 2b (≥2 members in top-30) must also be satisfied.
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

        # Gate 1: theme's known member stocks that are in today's top-30
        dict_tw = {s["code"] for s in theme.get("tw_stocks", [])}
        dict_us = {s["ticker"] for s in theme.get("us_stocks", [])}
        dict_members = dict_tw | dict_us
        candidates = [t for t in all_focal_codes if t in dict_members]
        if not candidates:
            continue

        # Gate 2a: keyword scoring per candidate
        keyword_focal: list[FocalStock] = []
        all_candidate_focal: list[FocalStock] = []
        for ticker in candidates:
            arts = ticker_arts.get(ticker, [])
            score, primary_hits = _score_articles(arts, keyword)
            info = stocks[ticker]
            fs = FocalStock(
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
            )
            all_candidate_focal.append(fs)
            if score >= MIN_SCORE:
                keyword_focal.append(fs)

        # Decide which pathway applies
        keyword_confirmed = len(keyword_focal) >= MIN_FOCAL
        volume_signal = len(all_candidate_focal) >= MIN_VOLUME

        if keyword_confirmed:
            focal_stocks = keyword_focal
            volume_only = False
        elif volume_signal:
            focal_stocks = all_candidate_focal
            volume_only = True
        else:
            continue

        focal_stocks.sort(key=lambda s: (-s.score, s.rank))

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
            volume_only=volume_only,
        ))

    return sorted(
        clusters,
        # keyword-confirmed first, then volume-only; within each group by members & score
        key=lambda c: (c.volume_only, -c.primary_art_count, -len(c.focal), -c.total_score),
    )
