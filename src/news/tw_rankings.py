#!/usr/bin/env python3
"""TWSE top-30 stocks by trading value (成交金額).

Data source: TWSE open data API (no auth required).
Also checks 漲停 in first 30 min via TWSE MI_INDEX20 endpoint.
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg
from dotenv import load_dotenv

load_dotenv()

TWSE_ALL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL"
TWSE_TOP  = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20"
TPEX_ALL  = "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php"

_HEADERS = {"User-Agent": "Mozilla/5.0 IIA-MarketData/1.0"}


def _last_trading_day() -> date:
    """Return most recent weekday (skip weekends)."""
    d = date.today()
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


def fetch_twse_top30(target_date: date | None = None) -> list[dict]:
    """Return top-30 TWSE stocks by 成交金額 on target_date."""
    if target_date is None:
        target_date = _last_trading_day()
    date_str = target_date.strftime("%Y%m%d")
    return _fetch_all_and_sort(date_str, target_date)


def _fetch_all_and_sort(date_str: str, target_date: date) -> list[dict]:
    """Download all TWSE stocks for the day, sort by 成交金額, return top 30.

    STOCK_DAY_ALL columns (10 cols):
      0:證券代號 1:證券名稱 2:成交股數 3:成交金額 4:開盤 5:最高 6:最低 7:收盤 8:漲跌價差 9:成交筆數
    """
    url = f"{TWSE_ALL}?date={date_str}"
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    if data.get("stat") != "OK" or not data.get("data"):
        raise RuntimeError(f"TWSE STOCK_DAY_ALL returned: {data.get('stat')}")

    parsed = []
    for row in data["data"]:
        try:
            ticker = str(row[0]).strip()
            name   = str(row[1]).strip()
            value  = float(str(row[3]).replace(",", ""))
            close  = float(str(row[7]).replace(",", "")) if row[7] not in ("--", "") else None
            diff_str = str(row[8]).replace(",", "").replace("+", "").strip()
            change_pct = None
            if close and diff_str and diff_str not in ("--", "X", ""):
                try:
                    diff = float(diff_str)
                    prev = close - diff
                    change_pct = round((close - prev) / prev * 100, 4) if prev != 0 else None
                except Exception:
                    pass
            parsed.append({"ticker": ticker, "name": name, "trading_value": value,
                           "close_price": close, "change_pct": change_pct,
                           "_open": float(str(row[4]).replace(",","")) if row[4] not in ("--","") else None,
                           "_high": float(str(row[5]).replace(",","")) if row[5] not in ("--","") else None})
        except Exception:
            continue

    parsed.sort(key=lambda x: x["trading_value"], reverse=True)
    return [{"rank": i + 1, **r} for i, r in enumerate(parsed[:30])]


def fetch_limit_up_30min(target_date: date | None = None) -> set[str]:
    """Return set of tickers that hit 漲停 in first 30 min (best-effort)."""
    # TWSE provides 開盤漲停 data in MI_INDEX endpoint
    # We check if open == high == 漲停價 as a proxy
    # This is approximate — a real 30-min check would need intraday data
    if target_date is None:
        target_date = _last_trading_day()
    date_str = target_date.strftime("%Y%m%d")

    try:
        url = f"{TWSE_ALL}?date={date_str}"
        req = Request(url, headers=_HEADERS)
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read())

        if data.get("stat") != "OK":
            return set()

        # STOCK_DAY_ALL cols: 0:代號 1:名稱 2:成交股數 3:成交金額 4:開盤 5:最高 6:最低 7:收盤 8:漲跌 9:筆數
        limit_up = set()
        for row in data["data"]:
            try:
                ticker = str(row[0]).strip()
                close  = float(str(row[7]).replace(",", "")) if row[7] not in ("--","") else None
                high   = float(str(row[5]).replace(",", "")) if row[5] not in ("--","") else None
                open_  = float(str(row[4]).replace(",", "")) if row[4] not in ("--","") else None
                diff_str = str(row[8]).replace(",", "").replace("+","").strip()
                if close and high and open_ and diff_str not in ("--","X",""):
                    diff = float(diff_str)
                    prev = close - diff
                    pct  = (close - prev) / prev * 100 if prev != 0 else 0
                    # limit-up: ≥9.5% gain AND opened at or near high (locked limit-up from open)
                    if pct >= 9.5 and open_ >= high * 0.995:
                        limit_up.add(ticker)
            except Exception:
                continue
        return limit_up
    except Exception as e:
        print(f"  [limit_up] {e}")
        return set()


async def store_tw_rankings(conn, rows: list[dict], target_date: date,
                             limit_up: set[str]) -> int:
    count = 0
    for row in rows:
        is_lu = row["ticker"] in limit_up
        await conn.execute(
            """INSERT INTO trading_rankings
               (rank_date, market, rank, ticker, name, trading_value,
                close_price, change_pct, is_limit_up_30m)
               VALUES ($1,'TW',$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (rank_date, market, ticker) DO UPDATE
               SET rank=$2, trading_value=$5, close_price=$6,
                   change_pct=$7, is_limit_up_30m=$8""",
            target_date, row["rank"], row["ticker"], row.get("name"),
            row["trading_value"], row.get("close_price"), row.get("change_pct"), is_lu,
        )
        count += 1
    return count


async def fetch_and_store(target_date: date | None = None) -> int:
    if target_date is None:
        target_date = _last_trading_day()

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    print(f"Fetching TW trading rankings for {target_date}…")

    try:
        top30 = fetch_twse_top30(target_date)
        limit_up = fetch_limit_up_30min(target_date)
        print(f"  Top 30 fetched, {len(limit_up)} limit-up stocks detected")
        for r in top30[:5]:
            lu = " 漲停" if r["ticker"] in limit_up else ""
            pct = f"{r['change_pct']:+.2f}%" if r["change_pct"] else "  N/A"
            val_b = r["trading_value"] / 1e8
            print(f"  #{r['rank']:2d} {r['ticker']} {r.get('name','')[:8]:8s}  {val_b:.1f}億  {pct}{lu}")
        n = await store_tw_rankings(conn, top30, target_date, limit_up)
        print(f"  Stored {n} rows")
    except Exception as e:
        print(f"  ERROR: {e}")
        n = 0

    await conn.close()
    return n


if __name__ == "__main__":
    asyncio.run(fetch_and_store())
