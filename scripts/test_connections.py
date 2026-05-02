#!/usr/bin/env python3
"""Test database and Chrome debug port connections."""
import asyncio
import os

import asyncpg
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()


async def test_db():
    print("=== Database ===")
    try:
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        version = await conn.fetchval("SELECT version()")
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        await conn.close()
        print(f"OK: {version[:50]}...")
        print(f"Tables: {[r['tablename'] for r in tables]}")
    except Exception as e:
        print(f"FAIL: {e}")


async def test_chrome():
    print("\n=== Chrome Debug Port ===")
    port = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{port}")
            contexts = browser.contexts
            pages = [page.url for ctx in contexts for page in ctx.pages]
            print(f"OK: connected, {len(pages)} page(s) open")
            if pages:
                print(f"Pages: {pages[:3]}")
            await browser.close()
    except Exception as e:
        print(f"FAIL: {e}")
        print("  Make sure Chrome is running with: --remote-debugging-port=9222")


async def main():
    await test_db()
    await test_chrome()


if __name__ == "__main__":
    asyncio.run(main())
