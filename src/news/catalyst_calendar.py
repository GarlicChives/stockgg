#!/usr/bin/env python3
"""Catalyst calendar — upcoming earnings + macro events for the next 14 days.

Sources (all free):
  - yfinance.Ticker.get_earnings_dates() per ticker (TW + US)
  - Hard-coded macro events in data/calendar_macro.json (FOMC, conferences)

Target tickers are picked from:
  - Latest trading_rankings (top movers by volume)
  - Articles' tickers[] mentioned >= 3 times in last 14 days

Storage: catalyst_events table (auto-created). Auto rows are refreshed each
day; manual entries (source != 'auto') survive across refreshes.
"""
import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils import db

MACRO_EVENTS_FILE = Path(__file__).resolve().parents[2] / "data" / "calendar_macro.json"

# Initial seed if data/calendar_macro.json is missing. Edit the JSON file
# (not this list) to maintain — JSON wins when present.
DEFAULT_MACRO_EVENTS = [
    {"date": "2026-06-17", "type": "fomc",       "title": "FOMC 利率決議",          "importance": 3},
    {"date": "2026-07-29", "type": "fomc",       "title": "FOMC 利率決議",          "importance": 3},
    {"date": "2026-09-16", "type": "fomc",       "title": "FOMC 利率決議",          "importance": 3},
    {"date": "2026-10-28", "type": "fomc",       "title": "FOMC 利率決議",          "importance": 3},
    {"date": "2026-12-16", "type": "fomc",       "title": "FOMC 利率決議",          "importance": 3},
    {"date": "2026-06-02", "type": "conference", "title": "Computex 2026 開展",    "importance": 3},
    {"date": "2026-06-08", "type": "conference", "title": "WWDC 2026",            "importance": 2},
    {"date": "2026-10-13", "type": "conference", "title": "NVIDIA GTC Fall 2026", "importance": 3},
]

HIGH_IMPORTANCE_TICKERS = {"2330", "2454", "2317", "NVDA", "TSM", "AAPL", "AMD", "MSFT", "GOOGL", "META"}


def _load_macro_events() -> list[dict]:
    if MACRO_EVENTS_FILE.exists():
        try:
            return json.loads(MACRO_EVENTS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ⚠ macro events JSON parse failed ({exc}) — using defaults")
    return DEFAULT_MACRO_EVENTS


def _yf_symbol(ticker: str, market: str) -> str:
    if market == "TW" and not ticker.endswith(".TW") and not ticker.endswith(".TWO"):
        return f"{ticker}.TW"
    return ticker


def _fetch_one_earnings(ticker: str, market: str, days_ahead: int) -> list[dict]:
    import yfinance as yf
    sym = _yf_symbol(ticker, market)
    try:
        edates = yf.Ticker(sym).get_earnings_dates(limit=4)
    except Exception:
        return []
    if edates is None or len(edates) == 0:
        return []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)
    out: list[dict] = []
    for idx in edates.index:
        try:
            dt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.fromisoformat(str(idx))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt < now - timedelta(days=1) or dt > cutoff:
            continue
        out.append({
            "date": dt.date(),
            "ticker": ticker,
            "market": market,
            "title": f"{ticker} 法說會 / Earnings",
            "type": "earnings",
            "importance": 3 if ticker in HIGH_IMPORTANCE_TICKERS else 2,
        })
    return out


async def _collect_target_tickers(conn, max_total: int = 80) -> list[tuple[str, str]]:
    rank_date = await conn.fetchval("SELECT MAX(rank_date) FROM trading_rankings")
    seen: set[tuple[str, str]] = set()

    if rank_date:
        for r in await conn.fetch(
            """SELECT ticker, market FROM trading_rankings
               WHERE rank_date=$1
               ORDER BY trading_value DESC NULLS LAST LIMIT 60""",
            rank_date,
        ):
            if r["ticker"]:
                seen.add((r["ticker"], r["market"]))

    for r in await conn.fetch(
        """SELECT unnest(tickers) AS tk, COUNT(*) AS n
           FROM articles
           WHERE created_at > NOW() - INTERVAL '14 days'
             AND tickers IS NOT NULL AND array_length(tickers, 1) > 0
           GROUP BY tk
           HAVING COUNT(*) >= 3
           ORDER BY n DESC LIMIT 60"""
    ):
        tk = r["tk"] or ""
        if tk.isdigit() and len(tk) == 4:
            seen.add((tk, "TW"))
        elif tk.isalpha() and tk.isupper() and 2 <= len(tk) <= 5:
            seen.add((tk, "US"))

    return list(seen)[:max_total]


CREATE_TABLE_SQL = [
    """CREATE TABLE IF NOT EXISTS catalyst_events (
        id          SERIAL PRIMARY KEY,
        event_date  DATE NOT NULL,
        event_type  TEXT NOT NULL,
        ticker      TEXT NOT NULL DEFAULT '',
        title       TEXT NOT NULL,
        market      TEXT,
        source      TEXT NOT NULL DEFAULT 'auto',
        importance  INT DEFAULT 2,
        metadata    JSONB DEFAULT '{}',
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(event_date, event_type, ticker, title)
    )""",
    "CREATE INDEX IF NOT EXISTS catalyst_events_date_idx ON catalyst_events(event_date)",
]


async def fetch_and_store(days_ahead: int = 21) -> int:
    conn = await db.connect()
    try:
        for stmt in CREATE_TABLE_SQL:
            await conn.execute(stmt)

        # Wipe stale auto rows in the forward window so re-runs don't pile up
        await conn.execute(
            "DELETE FROM catalyst_events "
            "WHERE event_date >= CURRENT_DATE AND source = 'auto'"
        )

        target_tickers = await _collect_target_tickers(conn)
        print(f"  Fetching earnings for {len(target_tickers)} tickers…")

        loop = asyncio.get_running_loop()
        rows: list[dict] = []
        for ticker, market in target_tickers:
            rows.extend(await loop.run_in_executor(
                None, _fetch_one_earnings, ticker, market, days_ahead
            ))

        today = date.today()
        cutoff = today + timedelta(days=days_ahead)
        for ev in _load_macro_events():
            try:
                ev_date = datetime.fromisoformat(ev["date"]).date()
            except Exception:
                continue
            if ev_date < today or ev_date > cutoff:
                continue
            rows.append({
                "date": ev_date,
                "ticker": "",
                "market": None,
                "title": ev["title"],
                "type": ev.get("type", "other"),
                "importance": ev.get("importance", 2),
            })

        inserted = 0
        for r in rows:
            try:
                await conn.execute(
                    """INSERT INTO catalyst_events
                       (event_date, event_type, ticker, title, market, source, importance)
                       VALUES ($1, $2, $3, $4, $5, 'auto', $6)
                       ON CONFLICT (event_date, event_type, ticker, title)
                       DO UPDATE SET importance = EXCLUDED.importance,
                                     market     = EXCLUDED.market""",
                    r["date"], r["type"], r["ticker"], r["title"], r.get("market"), r["importance"],
                )
                inserted += 1
            except Exception as exc:
                print(f"  ⚠ insert failed for {r}: {exc}")

        print(f"  Stored {inserted} catalyst events (forward {days_ahead} days)")
        return inserted
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(fetch_and_store())
