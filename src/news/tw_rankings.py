#!/usr/bin/env python3
"""TWSE + TPEX top-30 stocks by trading value (成交金額).

Fetches both 上市 (TWSE) and 上櫃 (TPEX), merges and re-ranks top 30.
Data source: TWSE / TPEX open data APIs (no auth required).
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
TPEX_ALL = "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php"

_HEADERS = {"User-Agent": "Mozilla/5.0 IIA-MarketData/1.0"}


def _last_trading_day() -> date:
    """Return most recent weekday (skip weekends; holiday check done by API)."""
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ── TWSE (上市) ──────────────────────────────────────────────────────────────

def _fetch_twse_all(date_str: str) -> list[dict]:
    """Download all TWSE stocks for the day, return sorted list.

    STOCK_DAY_ALL columns:
      0:代號 1:名稱 2:成交股數 3:成交金額 4:開盤 5:最高 6:最低 7:收盤 8:漲跌 9:筆數
    """
    url = f"{TWSE_ALL}?date={date_str}"
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    if data.get("stat") != "OK" or not data.get("data"):
        raise RuntimeError(f"TWSE STOCK_DAY_ALL stat={data.get('stat')!r}")

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
            parsed.append({
                "ticker": ticker, "name": name, "trading_value": value,
                "close_price": close, "change_pct": change_pct,
                "board": "TWSE",
                "_open": float(str(row[4]).replace(",", "")) if row[4] not in ("--", "") else None,
                "_high": float(str(row[5]).replace(",", "")) if row[5] not in ("--", "") else None,
            })
        except Exception:
            continue

    parsed.sort(key=lambda x: x["trading_value"], reverse=True)
    return parsed


# ── TPEX (上櫃) ──────────────────────────────────────────────────────────────

def _fetch_tpex_all(date_str: str) -> list[dict]:
    """Download all TPEX stocks for the day, return sorted list.

    TPEX stk_quote_result aaData columns:
      0:代號 1:名稱 2:收盤 3:漲跌 4:開盤 5:最高 6:最低 7:均價
      8:成交股數(千股) 9:成交金額(千元) 10:成交筆數 ...
    """
    yyyy, mm, dd = date_str[:4], date_str[4:6], date_str[6:8]
    url = f"{TPEX_ALL}?d={yyyy}/{mm}/{dd}&s=0,asc,0&o=json&l=zh-tw"
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    aa = data.get("aaData") or []
    if not aa:
        raise RuntimeError(f"TPEX returned no aaData for {date_str}")

    parsed = []
    for row in aa:
        try:
            ticker = str(row[0]).strip()
            name   = str(row[1]).strip()
            if not ticker or not (ticker.isdigit() or ticker.isalnum()):
                continue

            val_str = str(row[9]).replace(",", "")
            if not val_str or val_str in ("--", ""):
                continue
            value = float(val_str) * 1000  # 千元 → 元

            close_str = str(row[2]).replace(",", "")
            close = float(close_str) if close_str not in ("--", "") else None

            diff_str = str(row[3]).replace(",", "").replace("+", "").strip()
            change_pct = None
            if close and diff_str and diff_str not in ("--", "X", ""):
                try:
                    diff = float(diff_str)
                    prev = close - diff
                    change_pct = round((close - prev) / prev * 100, 4) if prev != 0 else None
                except Exception:
                    pass

            open_str = str(row[4]).replace(",", "")
            high_str = str(row[5]).replace(",", "")
            parsed.append({
                "ticker": ticker, "name": name, "trading_value": value,
                "close_price": close, "change_pct": change_pct,
                "board": "TPEX",
                "_open": float(open_str) if open_str not in ("--", "") else None,
                "_high": float(high_str) if high_str not in ("--", "") else None,
            })
        except Exception:
            continue

    parsed.sort(key=lambda x: x["trading_value"], reverse=True)
    return parsed


# ── Limit-up detection ────────────────────────────────────────────────────────

def _compute_limit_up(rows: list[dict]) -> set[str]:
    """Return ticker set where stock appears to have opened locked-limit-up."""
    limit_up = set()
    for r in rows:
        try:
            close = r.get("close_price")
            open_ = r.get("_open")
            high  = r.get("_high")
            pct   = r.get("change_pct")
            if close and open_ and high and pct is not None:
                if pct >= 9.5 and open_ >= high * 0.995:
                    limit_up.add(r["ticker"])
        except Exception:
            pass
    return limit_up


# ── Fetch with holiday fallback ───────────────────────────────────────────────

def _fetch_with_fallback(fetch_fn, target_date: date, label: str) -> tuple[list[dict], date]:
    """Try fetch_fn for target_date, falling back up to 5 previous weekdays."""
    for days_back in range(6):
        try_date = target_date - timedelta(days=days_back)
        if try_date.weekday() >= 5:
            continue
        date_str = try_date.strftime("%Y%m%d")
        try:
            rows = fetch_fn(date_str)
            if rows:
                if days_back > 0:
                    print(f"  [{label}] Holiday fallback: using {try_date}")
                return rows, try_date
        except RuntimeError as e:
            print(f"  [{label}] {try_date}: {e} — trying previous day")
        except Exception as e:
            print(f"  [{label}] {try_date}: unexpected error {e}")
    raise RuntimeError(f"{label}: no valid data in last 5 weekdays")


# ── DB store ─────────────────────────────────────────────────────────────────

async def store_tw_rankings(conn, rows: list[dict], target_date: date,
                             limit_up: set[str]) -> int:
    count = 0
    for row in rows:
        is_lu = row["ticker"] in limit_up
        extra = json.dumps({"board": row.get("board", "TWSE")})
        await conn.execute(
            """INSERT INTO trading_rankings
               (rank_date, market, rank, ticker, name, trading_value,
                close_price, change_pct, is_limit_up_30m, extra)
               VALUES ($1,'TW',$2,$3,$4,$5,$6,$7,$8,$9)
               ON CONFLICT (rank_date, market, ticker) DO UPDATE
               SET rank=$2, trading_value=$5, close_price=$6,
                   change_pct=$7, is_limit_up_30m=$8, extra=$9""",
            target_date, row["rank"], row["ticker"], row.get("name"),
            row["trading_value"], row.get("close_price"), row.get("change_pct"),
            is_lu, extra,
        )
        count += 1
    return count


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_and_store(target_date: date | None = None) -> int:
    if target_date is None:
        target_date = _last_trading_day()

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    print(f"Fetching TW trading rankings (TWSE + TPEX) from {target_date}…")

    try:
        # Fetch TWSE
        twse_rows, twse_date = _fetch_with_fallback(_fetch_twse_all, target_date, "TWSE")
        print(f"  TWSE: {len(twse_rows)} stocks for {twse_date}")

        # Fetch TPEX (best-effort)
        tpex_rows: list[dict] = []
        tpex_date = twse_date
        try:
            tpex_rows, tpex_date = _fetch_with_fallback(_fetch_tpex_all, target_date, "TPEX")
            print(f"  TPEX: {len(tpex_rows)} stocks for {tpex_date}")
        except Exception as e:
            print(f"  TPEX: skipped ({e})")

        # Merge and re-rank top 30
        # Use the most common date (prefer TWSE date)
        actual_date = twse_date
        all_rows = twse_rows + tpex_rows
        all_rows.sort(key=lambda x: x["trading_value"], reverse=True)
        top30 = [{"rank": i + 1, **r} for i, r in enumerate(all_rows[:30])]

        # Compute limit-up from merged rows
        limit_up = _compute_limit_up(all_rows)

        print(f"  Top 30 merged ({twse_date}), {len(limit_up)} limit-up")
        for r in top30[:5]:
            lu = " 漲停" if r["ticker"] in limit_up else ""
            pct = f"{r['change_pct']:+.2f}%" if r["change_pct"] else "  N/A"
            board = r.get("board", "")[:4]
            print(f"  #{r['rank']:2d} {r['ticker']} {r.get('name','')[:8]:8s}"
                  f"  {r['trading_value']/1e8:.1f}億  {pct}  [{board}]{lu}")

        n = await store_tw_rankings(conn, top30, actual_date, limit_up)
        print(f"  Stored {n} rows for {actual_date}")

    except Exception as e:
        print(f"  ERROR: {e}")
        n = 0

    await conn.close()
    return n


if __name__ == "__main__":
    asyncio.run(fetch_and_store())
