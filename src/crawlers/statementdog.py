#!/usr/bin/env python3
"""StatementDog 財報狗 industry reports crawler — connects via CDP."""
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

SOURCE = "statementdog"
INDEX_URL = "https://statementdog.com/industry_reports"
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
LOOKBACK_DAYS = 180
CONCURRENCY = 2


def parse_date(date_str: str) -> Optional[datetime]:
    date_str = date_str.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_tickers(text: str) -> list[str]:
    tw = re.findall(r'\((\d{4,5})\)', text)
    us = re.findall(r'\(([A-Z]{1,5})\)', text)
    skip = {"QoQ", "YoY", "EPS", "CEO", "AI", "US", "TW", "GDP", "CPI", "PMI", "VIX",
            "ETF", "PCE", "HBM", "DDR", "TAM", "NB", "PC", "GAAP", "DRAM", "NAND",
            "GPU", "CPU", "PLP", "DUV", "EUV", "CPO", "USMCA"}
    all_t = list(dict.fromkeys(tw + us))
    return [t for t in all_t if t not in skip and len(t) >= 2]


async def collect_report_links(context: BrowserContext, cutoff: datetime) -> list[dict]:
    """Collect all industry report links and dates from index pages."""
    reports = []

    for page_num in range(1, 20):  # safe upper bound
        url = INDEX_URL if page_num == 1 else f"{INDEX_URL}?page={page_num}"
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1200)

        # Each card: anchor contains title + date in text
        items = await page.eval_on_selector_all(
            "a[href*='/industry_reports/']",
            r"""els => {
                const seen = new Set();
                return els.map(el => {
                    const href = el.href;
                    if (seen.has(href) || !href.match(/\/\d+$/)) return null;
                    seen.add(href);
                    const text = el.innerText || '';
                    const dateMatch = text.match(/(\d{4}\/\d{2}\/\d{2})/);
                    // Title is the first line before the date
                    const titleMatch = text.split('\n')[0].trim();
                    return {href, title: titleMatch.slice(0,120), date_str: dateMatch ? dateMatch[1] : ''};
                }).filter(Boolean);
            }"""
        )

        if not items:
            await page.close()
            break

        # Check if next page exists
        has_next = await page.locator(f"a[href*='page={page_num+1}']").count() > 0

        page_reports = []
        all_too_old = True
        for item in items:
            dt = parse_date(item["date_str"]) if item["date_str"] else None
            if dt and dt < cutoff:
                continue  # too old
            all_too_old = False
            page_reports.append({**item, "published_at": dt})

        reports.extend(page_reports)
        print(f"  Page {page_num}: {len(items)} reports, {len(page_reports)} within lookback")

        await page.close()

        if not has_next or all_too_old:
            break

    # Deduplicate
    seen = set()
    unique = []
    for r in reports:
        if r["href"] not in seen:
            seen.add(r["href"])
            unique.append(r)
    return unique


async def fetch_report(context: BrowserContext, url: str, title: str,
                       published_at: Optional[datetime]) -> Optional[dict]:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1500)

        # Scroll to load full content
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(600)

        # Title from page
        h1 = page.locator("h1").first
        page_title = (await h1.inner_text()).strip() if await h1.count() > 0 else ""
        if not page_title:
            page_title = re.sub(r'\s*-\s*財報狗.*$', '', await page.title()).strip()
        final_title = page_title or title

        # Date from article page if missing
        if published_at is None:
            for sel in ["time[datetime]", "[class*='date']"]:
                el = page.locator(sel).first
                if await el.count() > 0:
                    t = await el.inner_text()
                    dt_attr = await el.get_attribute("datetime")
                    published_at = parse_date(dt_attr or t.strip())
                    if published_at:
                        break

        # Content
        article_el = page.locator("article").first
        if await article_el.count() == 0:
            return None
        content = await article_el.inner_text()

        if len(content) < 200:
            return None

        return {
            "source": SOURCE,
            "url": url,
            "title": final_title,
            "author": "財報狗",
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

        print(f"Collecting report list (cutoff: {cutoff.date()})...")
        all_reports = await collect_report_links(context, cutoff)
        print(f"Found {len(all_reports)} reports within lookback")

        # Filter already-crawled
        to_fetch = []
        for r in all_reports:
            exists = await conn.fetchval("SELECT 1 FROM articles WHERE url=$1", r["href"])
            if not exists:
                to_fetch.append(r)
        print(f"{len(to_fetch)} new reports to fetch (skipping {len(all_reports)-len(to_fetch)} existing)")

        sem = asyncio.Semaphore(CONCURRENCY)

        async def bounded_fetch(item):
            async with sem:
                result = await fetch_report(context, item["href"], item["title"], item.get("published_at"))
                if result:
                    print(f"  [{str(result['published_at'])[:10]}] {result['title'][:60]} ({len(result['content'])} chars)")
                return result

        results = await asyncio.gather(*[bounded_fetch(r) for r in to_fetch])
        fetched = [r for r in results if r is not None]

        new_count = await upsert_articles(conn, fetched)
        print(f"\nDone. {new_count} new reports saved.")

        await browser.close()

    await conn.close()
    return new_count


if __name__ == "__main__":
    import sys
    asyncio.run(crawl(incremental="--incremental" in sys.argv))
