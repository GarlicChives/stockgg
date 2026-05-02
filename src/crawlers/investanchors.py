#!/usr/bin/env python3
"""InvestAnchors VIP content crawler — connects via CDP to user's Chrome session."""
import asyncio
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser

from src.utils.browser import connect_browser
from src.utils.refine import refine_and_store

load_dotenv()

SOURCE = "investanchors"
INDEX_URL = "https://investanchors.com/user/vip_contents/investanchors_index"
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
LOOKBACK_DAYS = 180  # ~6 months
CONCURRENCY = 3      # simultaneous article fetches


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse date strings from the index page (e.g. '2026-05-03')."""
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y年%m月%d日", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_tickers(text: str) -> list[str]:
    """Extract stock tickers mentioned in article text."""
    # Taiwan stocks: 4-digit numbers in parentheses, e.g. (2330) or 台積電(2330)
    tw = re.findall(r'\((\d{4,5})\)', text)
    # US stocks: uppercase letters 1-5 chars in parentheses, e.g. (NVDA) or TSMC(TSM)
    us = re.findall(r'\(([A-Z]{1,5})\)', text)
    # Also catch standalone US tickers mentioned with colons e.g. "NVDA："
    us2 = re.findall(r'\b([A-Z]{2,5})(?=：|:|\s*財報|\s*法說)', text)
    all_tickers = list(dict.fromkeys(tw + us + us2))
    # Filter out common false positives
    skip = {"QoQ", "YoY", "EPS", "CEO", "CFO", "COO", "CTO", "AI", "PC", "US", "TW", "GDP", "CPI", "PMI", "VIX"}
    return [t for t in all_tickers if t not in skip]


async def get_index_articles(browser: Browser, cutoff: datetime) -> list[dict]:
    """Collect all article metadata from index pages within lookback period."""
    context = browser.contexts[0]
    articles = []

    for page_num in range(1, 10):  # max 10 pages as safety
        url = INDEX_URL if page_num == 1 else f"{INDEX_URL}?page={page_num}"
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1000)

        # Extract article rows: get all vip_content links with their parent text for dates
        rows = await page.eval_on_selector_all(
            "a[href*='/vip_contents/']",
            r"""els => els.map(el => {
                const href = el.href;
                if (href.includes('index') || href.includes('favorite')) return null;
                // Get date from parent element text (format: "title\t2026-05-03\t views")
                const parentText = (el.closest('li') || el.parentElement || el).innerText || '';
                const dateMatch = parentText.match(/(\d{4}-\d{2}-\d{2})/);
                return {
                    href: href,
                    title: el.innerText.trim(),
                    date_str: dateMatch ? dateMatch[1] : ''
                };
            }).filter(Boolean)"""
        )

        # Filter out non-article links
        page_articles = [
            r for r in rows
            if "/vip_contents/" in r["href"] and "index" not in r["href"] and "favorite" not in r["href"]
        ]

        if not page_articles:
            await page.close()
            break

        # Check dates — stop pagination if all articles on this page are too old
        valid = []
        all_too_old = True
        for r in page_articles:
            dt = parse_date(r["date_str"]) if r["date_str"] else None
            if dt is None or dt >= cutoff:
                valid.append({**r, "published_at": dt})
                all_too_old = False
            # If no date parsed, include anyway and let article-level check decide

        articles.extend(valid)
        print(f"  Page {page_num}: {len(page_articles)} rows, {len(valid)} within lookback")

        await page.close()

        if all_too_old:
            print(f"  All articles on page {page_num} older than cutoff, stopping.")
            break

    # Deduplicate by URL
    seen = set()
    unique = []
    for a in articles:
        if a["href"] not in seen:
            seen.add(a["href"])
            unique.append(a)
    return unique


async def fetch_article(context, url: str, title: str, published_at: Optional[datetime]) -> Optional[dict]:
    """Fetch full content of a single article."""
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # Check if we hit a login wall
        content_el = page.locator("#content")
        if await content_el.count() == 0:
            print(f"  [SKIP] No #content found (login wall?): {url}")
            return None

        content_text = await content_el.inner_text()

        # Extract title from page if not from index
        page_title = await page.title()
        clean_title = page_title.replace(" | 定錨產業筆記", "").strip() if page_title else title

        # Try to extract date from article if not already known
        if published_at is None:
            date_match = re.search(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日', content_text)
            if date_match:
                y, m, d = date_match.groups()
                try:
                    published_at = datetime(int(y), int(m), int(d), tzinfo=timezone.utc)
                except ValueError:
                    pass

        tickers = extract_tickers(content_text)

        return {
            "source": SOURCE,
            "url": url,
            "title": clean_title,
            "author": "定錨產業筆記",
            "published_at": published_at,
            "content": content_text.strip(),
            "tickers": tickers,
        }
    except Exception as e:
        print(f"  [ERROR] {url}: {e}")
        return None
    finally:
        await page.close()


async def upsert_articles(conn: asyncpg.Connection, articles: list[dict]) -> int:
    """Insert new articles, skip existing ones. Returns count of new inserts."""
    new_count = 0
    for art in articles:
        existing = await conn.fetchval(
            "SELECT id FROM articles WHERE url = $1", art["url"]
        )
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
    """
    incremental=False: first-time crawl, fetches last LOOKBACK_DAYS articles
    incremental=True:  daily update, only fetches articles newer than latest in DB
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    db_url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(db_url)

    if incremental:
        latest = await conn.fetchval(
            "SELECT MAX(published_at) FROM articles WHERE source=$1", SOURCE
        )
        if latest:
            cutoff = latest.replace(tzinfo=timezone.utc) if latest.tzinfo is None else latest
            print(f"Incremental mode: fetching articles after {cutoff.date()}")

    async with async_playwright() as p:
        browser = await connect_browser(p)
        context = browser.contexts[0]

        print(f"Collecting article list (cutoff: {cutoff.date()})...")
        index_articles = await get_index_articles(browser, cutoff)
        print(f"Found {len(index_articles)} articles to process")

        # Filter out already-crawled URLs
        to_fetch = []
        for art in index_articles:
            exists = await conn.fetchval("SELECT 1 FROM articles WHERE url=$1", art["href"])
            if not exists:
                to_fetch.append(art)
        print(f"{len(to_fetch)} new articles to fetch (skipping {len(index_articles)-len(to_fetch)} existing)")

        # Fetch articles with limited concurrency
        sem = asyncio.Semaphore(CONCURRENCY)

        async def bounded_fetch(art):
            async with sem:
                result = await fetch_article(context, art["href"], art["title"], art.get("published_at"))
                if result:
                    print(f"  Fetched: {result['title'][:60]} ({len(result['content'])} chars)")
                return result

        results = await asyncio.gather(*[bounded_fetch(a) for a in to_fetch])
        fetched = [r for r in results if r is not None]

        # Save to database
        new_count = await upsert_articles(conn, fetched)
        print(f"\nDone. {new_count} new articles saved to database.")

        await browser.close()

    await conn.close()
    return new_count


if __name__ == "__main__":
    import sys
    incremental = "--incremental" in sys.argv
    asyncio.run(crawl(incremental=incremental))
