#!/usr/bin/env python3
"""Market data fetcher — indices, VIX, 10Y yield, Fear & Greed.

Stores results in market_snapshots table.
Run daily after market close (US: ~06:00 TW time next day).
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# Symbols: (yfinance_symbol, market, display_name)
TRACKED = [
    ("^GSPC",  "US", "S&P 500"),
    ("^IXIC",  "US", "NASDAQ"),
    ("^SOX",   "US", "Philadelphia SOX"),
    ("^DJI",   "US", "Dow Jones"),
    ("^TWII",  "TW", "加權指數"),
    ("^N225",  "JP", "日經 225"),
    ("^VIX",     "INDICATOR", "VIX"),
    ("^TNX",     "INDICATOR", "10Y 殖利率"),
    ("DX-Y.NYB", "INDICATOR", "美元指數"),
]


def fetch_fear_and_greed() -> float | None:
    """Fetch CNN Fear & Greed index (0–100). Returns None if unavailable."""
    endpoints = [
        "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
        "https://fear-greed-index.p.rapidapi.com/v1/fgi",
    ]
    for url in endpoints:
        try:
            req = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://edition.cnn.com/",
            })
            with urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            score = (data.get("fear_and_greed") or {}).get("score")
            if score is not None:
                return float(score)
        except Exception:
            continue
    return None


def fetch_yfinance_snapshot(target_date: date) -> dict[str, dict]:
    """Download 2-day OHLCV for all symbols, return {symbol: {close, change_pct, volume}}."""
    symbols = [s for s, _, _ in TRACKED]
    raw = yf.download(
        symbols,
        period="5d",       # fetch 5 days to ensure we get the last trading day
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    result: dict[str, dict] = {}
    for sym, market, name in TRACKED:
        try:
            closes = raw["Close"][sym].dropna()
            if len(closes) < 2:
                continue
            today_close = float(closes.iloc[-1])
            prev_close  = float(closes.iloc[-2])
            change_pct  = (today_close - prev_close) / prev_close * 100

            vol_series = raw.get("Volume", {}).get(sym)
            volume = int(vol_series.dropna().iloc[-1]) if vol_series is not None else None

            actual_date = closes.index[-1].date()
            result[sym] = {
                "market": market,
                "name": name,
                "snapshot_date": actual_date,
                "close_price": today_close,
                "change_pct": change_pct,
                "volume": volume,
            }
        except Exception as e:
            print(f"  [{sym}] error: {e}")

    return result


async def store_snapshots(conn, snapshots: dict[str, dict], fear_greed: float | None):
    count = 0
    for symbol, d in snapshots.items():
        await conn.execute(
            """INSERT INTO market_snapshots
               (snapshot_date, market, symbol, close_price, change_pct, volume, extra)
               VALUES ($1,$2,$3,$4,$5,$6,$7)
               ON CONFLICT (snapshot_date, market, symbol) DO UPDATE
               SET close_price=$4, change_pct=$5, volume=$6, extra=$7""",
            d["snapshot_date"], d["market"], symbol,
            d["close_price"], round(d["change_pct"], 4), d["volume"],
            json.dumps({"name": d["name"]}),
        )
        count += 1

    if fear_greed is not None:
        today = date.today()
        await conn.execute(
            """INSERT INTO market_snapshots
               (snapshot_date, market, symbol, close_price, change_pct, volume, extra)
               VALUES ($1,'INDICATOR','FEAR_GREED',$2,NULL,NULL,$3)
               ON CONFLICT (snapshot_date, market, symbol) DO UPDATE
               SET close_price=$2""",
            today, fear_greed, json.dumps({"name": "Fear & Greed"}),
        )
        count += 1

    return count


async def fetch_and_store(target_date: date | None = None) -> int:
    if target_date is None:
        target_date = date.today()

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    print(f"Fetching market data for {target_date}…")
    snapshots = fetch_yfinance_snapshot(target_date)
    print(f"  Got {len(snapshots)} symbols from Yahoo Finance")

    fear_greed = fetch_fear_and_greed()
    if fear_greed is not None:
        print(f"  Fear & Greed: {fear_greed:.1f}")

    for sym, d in snapshots.items():
        sign = "+" if d["change_pct"] >= 0 else ""
        print(f"  {sym:12s}  {d['close_price']:>10.2f}  {sign}{d['change_pct']:.2f}%")

    n = await store_snapshots(conn, snapshots, fear_greed)
    print(f"Stored {n} rows to market_snapshots")
    await conn.close()
    return n


if __name__ == "__main__":
    asyncio.run(fetch_and_store())
