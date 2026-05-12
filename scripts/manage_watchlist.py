#!/usr/bin/env python3
"""Watchlist + thesis CLI.

Usage:
    uv run scripts/manage_watchlist.py list
    uv run scripts/manage_watchlist.py add <ticker> <market> "<thesis>" [--name <名稱>] [--target <價格>]
    uv run scripts/manage_watchlist.py remove <id>
    uv run scripts/manage_watchlist.py deactivate <id>
    uv run scripts/manage_watchlist.py signals [<id>]     # show recent verdicts
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from src.utils import db
from src.analysis.thesis_check import ensure_schema


async def cmd_list(_args) -> None:
    conn = await db.connect()
    try:
        await ensure_schema(conn)
        rows = await conn.fetch(
            """SELECT w.id, w.ticker, w.market, w.name, w.is_active, w.target_price,
                      w.thesis, w.thesis_added_at, w.last_checked_at,
                      (SELECT verdict FROM thesis_signals
                       WHERE watchlist_id=w.id ORDER BY check_date DESC LIMIT 1) AS last_verdict
               FROM watchlist w ORDER BY w.is_active DESC, w.id"""
        )
        if not rows:
            print("(空 watchlist)")
            return
        print(f"{'ID':>3} {'活躍':<4} {'代號':<6} {'市場':<3} {'名稱':<14} {'目標':<8} {'最新':<5}  論點")
        print("-" * 110)
        for r in rows:
            active = "✓" if r["is_active"] else "—"
            tgt = f"{float(r['target_price']):.0f}" if r["target_price"] else "—"
            verdict_map = {"supportive": "✅", "neutral": "▫", "contradicting": "⚠", None: ""}
            v = verdict_map.get(r["last_verdict"], "")
            thesis_preview = (r["thesis"] or "(無)")[:60]
            name = (r["name"] or "")[:12]
            print(f"{r['id']:>3} {active:<4} {r['ticker']:<6} {r['market'] or '':<3} {name:<14} {tgt:<8} {v:<5}  {thesis_preview}")
    finally:
        await conn.close()


async def cmd_add(args) -> None:
    conn = await db.connect()
    try:
        await ensure_schema(conn)
        new_id = await conn.fetchval(
            """INSERT INTO watchlist (ticker, market, name, thesis, target_price, is_active, added_reason, thesis_added_at)
               VALUES ($1, $2, $3, $4, $5, TRUE, 'cli', NOW())
               RETURNING id""",
            args.ticker.upper(), args.market.upper(), args.name,
            args.thesis, args.target,
        )
        print(f"✓ Added id={new_id}  {args.ticker} ({args.market})  thesis: {args.thesis[:60]}")
    finally:
        await conn.close()


async def cmd_remove(args) -> None:
    conn = await db.connect()
    try:
        result = await conn.execute("DELETE FROM watchlist WHERE id=$1", args.id)
        print(f"DELETE result: {result}")
    finally:
        await conn.close()


async def cmd_deactivate(args) -> None:
    conn = await db.connect()
    try:
        result = await conn.execute("UPDATE watchlist SET is_active=FALSE WHERE id=$1", args.id)
        print(f"UPDATE result: {result}")
    finally:
        await conn.close()


async def cmd_signals(args) -> None:
    conn = await db.connect()
    try:
        await ensure_schema(conn)
        if args.id is not None:
            rows = await conn.fetch(
                """SELECT s.check_date, w.ticker, s.verdict, s.summary, s.key_evidence
                   FROM thesis_signals s JOIN watchlist w ON w.id=s.watchlist_id
                   WHERE s.watchlist_id=$1 ORDER BY s.check_date DESC LIMIT 30""",
                args.id,
            )
        else:
            rows = await conn.fetch(
                """SELECT s.check_date, w.ticker, s.verdict, s.summary, s.key_evidence
                   FROM thesis_signals s JOIN watchlist w ON w.id=s.watchlist_id
                   ORDER BY s.check_date DESC, w.ticker LIMIT 50"""
            )
        if not rows:
            print("(無紀錄)")
            return
        verdict_map = {"supportive": "✅ 支持", "neutral": "▫ 中立", "contradicting": "⚠ 矛盾"}
        for r in rows:
            v = verdict_map.get(r["verdict"], r["verdict"])
            print(f"{r['check_date']} {r['ticker']:<6} {v}  {r['summary']}")
            for ev in (r["key_evidence"] or []):
                print(f"            · {ev}")
    finally:
        await conn.close()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    a = sub.add_parser("add")
    a.add_argument("ticker")
    a.add_argument("market", choices=["TW", "US", "tw", "us"])
    a.add_argument("thesis", help="一段話的投資論點")
    a.add_argument("--name", default=None)
    a.add_argument("--target", type=float, default=None, help="目標價（可選）")

    r = sub.add_parser("remove")
    r.add_argument("id", type=int)

    d = sub.add_parser("deactivate")
    d.add_argument("id", type=int)

    s = sub.add_parser("signals")
    s.add_argument("id", type=int, nargs="?")

    args = p.parse_args()
    asyncio.run({
        "list": cmd_list, "add": cmd_add, "remove": cmd_remove,
        "deactivate": cmd_deactivate, "signals": cmd_signals,
    }[args.cmd](args))


if __name__ == "__main__":
    main()
