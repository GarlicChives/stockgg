#!/usr/bin/env python3
"""Vocus 方格子 韭菜王 crawler — connects via CDP to user's Chrome session."""
import asyncio
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.utils import db
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext

from src.utils.browser import connect_browser
from src.utils.refine import refine_and_store

load_dotenv()

SOURCE = "vocus_chivesking"
INDEX_URL = "https://vocus.cc/salon/ChivesKing"
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
LOOKBACK_DAYS = 180
CONCURRENCY = 2


def extract_tickers(text: str) -> list[str]:
    tw = re.findall(r'\((\d{4,5})\)', text)
    us = re.findall(r'\b([A-Z]{2,5})\b(?=\s*(?:財報|法說|1Q|2Q|3Q|4Q|Q\d))', text)
    us2 = re.findall(r'\(([A-Z]{2,5})\)', text)
    skip = {"QoQ", "YoY", "EPS", "CEO", "CFO", "AI", "US", "TW", "GDP", "CPI", "PMI",
            "VIX", "ETF", "PCE", "PPI", "ISM", "IPO", "GAAP", "CAPEX", "CAGR",
            "DCI", "DUV", "EUV", "CPO", "GPU", "API", "NB", "PC"}
    all_t = list(dict.fromkeys(tw + us2 + us))
    return [t for t in all_t if t not in skip and len(t) >= 2]


async def collect_article_urls(context: BrowserContext, cutoff: datetime,
                               conn=None) -> list[str]:
    """Scroll the salon index to collect article URLs.

    conn: if provided (incremental mode), stop early when new URLs are all already in DB.
    """
    page = await context.new_page()
    await page.goto(INDEX_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    prev_count = 0
    all_hrefs: list[str] = []

    for i in range(30):  # max 30 scrolls
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)

        hrefs = await page.eval_on_selector_all(
            "a[href*='/article/']",
            "els => [...new Set(els.map(el => el.href))].filter(h => h.includes('vocus.cc/article/'))"
        )
        all_hrefs = list(dict.fromkeys(hrefs))

        if len(all_hrefs) == prev_count and i > 2:
            print(f"  Scroll stopped at {len(all_hrefs)} articles (scroll {i+1})")
            break

        # Incremental early stop: if all newly found URLs are already in DB, stop scrolling
        if conn and len(all_hrefs) > prev_count:
            new_urls = all_hrefs[prev_count:]
            existing = {r['url'] for r in await conn.fetch(
                "SELECT url FROM articles WHERE url = ANY($1::text[])", new_urls
            )}
            if existing.issuperset(new_urls):
                print(f"  Early stop at scroll {i+1}: all new URLs already in DB")
                break

        prev_count = len(all_hrefs)

    await page.close()
    print(f"Collected {len(all_hrefs)} unique article URLs from index")
    return all_hrefs


async def fetch_article(context: BrowserContext, url: str) -> Optional[dict]:
    """Fetch full content of a single Vocus article."""
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # Scroll to trigger full content render
        for _ in range(4):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(600)

        # Title
        title_el = page.locator("h1").first
        title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else ""
        if not title:
            page_title = await page.title()
            title = re.sub(r'\s*[|\-].*$', '', page_title).strip()

        # Date
        published_at = None
        time_el = page.locator("time[datetime]").first
        if await time_el.count() > 0:
            dt_str = await time_el.get_attribute("datetime")
            if dt_str:
                try:
                    published_at = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
                except ValueError:
                    pass

        # Check cutoff
        if published_at:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)
            if published_at < cutoff:
                return None  # too old, signal to stop

        # Content — prefer editor-content-block, fallback to main
        content = ""
        editor_el = page.locator("[class*='editor-content-block']").first
        if await editor_el.count() > 0:
            content = await editor_el.inner_text()

        if len(content) < 200:
            main_el = page.locator("main").first
            if await main_el.count() > 0:
                content = await main_el.inner_text()

        if len(content) < 100:
            return None

        # Check for paywall (very short content with subscription prompt)
        paywall_keywords = ["訂閱後可閱讀", "訂閱沙龍", "加入會員"]
        for kw in paywall_keywords:
            if kw in content and len(content) < 500:
                print(f"  [PAYWALL?] {url} ({len(content)} chars)")
                break

        return {
            "source": SOURCE,
            "url": url,
            "title": title,
            "author": "韭菜王",
            "published_at": published_at,
            "content": content.strip(),
            "tickers": extract_tickers(content),
        }
    except Exception as e:
        print(f"  [ERROR] {url}: {e}")
        return None
    finally:
        await page.close()


async def upsert_articles(conn, articles: list[dict]) -> int:
    new_count = 0
    for art in articles:
        existing = await conn.fetchval("SELECT id FROM articles WHERE url=$1", art["url"])
        if existing:
            continue
        row_id = await conn.fetchval(
            """INSERT INTO articles
               (source, url, title, author, published_at, content, tickers, status)
               VALUES ($1,$2,$3,$4,$5,$6,$7,'active') RETURNING id""",
            art["source"], art["url"], art["title"], art["author"],
            art["published_at"], art["content"], art["tickers"],
        )
        new_count += 1
        await refine_and_store(conn, row_id, art["title"], art["content"])
    return new_count


async def crawl(incremental: bool = False):
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    conn = await db.connect()

    if incremental:
        latest = await conn.fetchval(
            "SELECT MAX(published_at) FROM articles WHERE source=$1", SOURCE
        )
        if latest:
            cutoff = latest.replace(tzinfo=timezone.utc) if latest.tzinfo is None else latest
            print(f"Incremental: fetching after {cutoff.date()}")

    async with async_playwright() as p:
        browser = await connect_browser(p)
        context = browser.contexts[0]

        print("Collecting article URLs from index...")
        all_urls = await collect_article_urls(context, cutoff, conn=conn if incremental else None)

        # Filter already-crawled
        to_fetch = []
        for url in all_urls:
            exists = await conn.fetchval("SELECT 1 FROM articles WHERE url=$1", url)
            if not exists:
                to_fetch.append(url)
        print(f"{len(to_fetch)} new articles to fetch (skipping {len(all_urls)-len(to_fetch)} existing)")

        sem = asyncio.Semaphore(CONCURRENCY)
        results = []
        too_old_count = 0

        async def bounded_fetch(url):
            nonlocal too_old_count
            async with sem:
                result = await fetch_article(context, url)
                if result is None:
                    too_old_count += 1
                    return None
                print(f"  [{str(result['published_at'])[:10]}] {result['title'][:60]} ({len(result['content'])} chars)")
                return result

        fetched_raw = await asyncio.gather(*[bounded_fetch(u) for u in to_fetch])
        fetched = [r for r in fetched_raw if r is not None]

        new_count = await upsert_articles(conn, fetched)
        print(f"\nDone. {new_count} new articles saved ({too_old_count} skipped as too old).")

        await browser.close()

    await conn.close()
    return new_count


if __name__ == "__main__":
    import sys
    asyncio.run(crawl(incremental="--incremental" in sys.argv))
