#!/usr/bin/env python3
"""US top-30 stocks by trading value (price × volume).

Uses Yahoo Finance 'most active' screener — no auth required.
"""
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Yahoo Finance most-actives screener (by dollar volume)
YF_SCREENER = (
    "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    "?scrIds=most_actives&count=50&start=0"
)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def fetch_us_top30() -> list[dict]:
    """Return top-30 US stocks by trading value from Yahoo Finance screener."""
    req = Request(YF_SCREENER, headers=_HEADERS)
    with urlopen(req, timeout=15) as r:
        data = json.loads(r.read())

    quotes = (
        data.get("finance", {})
            .get("result", [{}])[0]
            .get("quotes", [])
    )

    rows = []
    rank = 0
    for q in quotes:
        ticker = q.get("symbol", "")
        if not ticker or "=" in ticker:   # skip ETFs with "=" in name
            continue
        price   = q.get("regularMarketPrice")
        volume  = q.get("regularMarketVolume")
        if not price or not volume:
            continue

        trading_value = price * volume
        change_pct    = q.get("regularMarketChangePercent")
        name          = q.get("shortName") or q.get("longName") or ""

        rank += 1
        rows.append({
            "rank": rank,
            "ticker": ticker,
            "name": name[:60],
            "trading_value": trading_value,
            "close_price": price,
            "change_pct": round(change_pct, 4) if change_pct is not None else None,
        })
        if rank >= 30:
            break

    # Sort by trading value (screener returns by volume, not dollar value)
    rows.sort(key=lambda x: x["trading_value"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


async def store_us_rankings(conn, rows: list[dict], target_date: date) -> int:
    count = 0
    for row in rows:
        await conn.execute(
            """INSERT INTO trading_rankings
               (rank_date, market, rank, ticker, name, trading_value, close_price, change_pct)
               VALUES ($1,'US',$2,$3,$4,$5,$6,$7)
               ON CONFLICT (rank_date, market, ticker) DO UPDATE
               SET rank=$2, trading_value=$5, close_price=$6, change_pct=$7""",
            target_date, row["rank"], row["ticker"], row["name"],
            row["trading_value"], row["close_price"], row.get("change_pct"),
        )
        count += 1
    return count


async def fetch_and_store(target_date: date | None = None) -> int:
    if target_date is None:
        target_date = date.today()

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    print(f"Fetching US trading rankings for {target_date}…")

    try:
        top30 = fetch_us_top30()
        print(f"  Got {len(top30)} stocks")
        for r in top30[:5]:
            val_b = r["trading_value"] / 1e9
            pct   = f"{r['change_pct']:+.2f}%" if r["change_pct"] is not None else "  N/A"
            print(f"  #{r['rank']:2d} {r['ticker']:6s} {r['name'][:20]:20s}  ${val_b:.1f}B  {pct}")

        n = await store_us_rankings(conn, top30, target_date)
        print(f"  Stored {n} rows")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()
        n = 0

    await conn.close()
    return n


if __name__ == "__main__":
    asyncio.run(fetch_and_store())
