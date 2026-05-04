#!/usr/bin/env python3
"""MacroMicro 財經M平方 crawler — blog articles + monthly reports + EDM quick reports."""
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

SOURCE = "macromicro"
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
LOOKBACK_DAYS = 180
CONCURRENCY = 2


def parse_date(date_str: str) -> Optional[datetime]:
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_tickers(text: str) -> list[str]:
    tw = re.findall(r'\((\d{4,5})\)', text)
    us = re.findall(r'\(([A-Z]{1,5})\)', text)
    skip = {"GAAP", "CEO", "CFO", "TGA", "QoQ", "YoY", "EPS", "AI", "US", "TW",
            "GDP", "CPI", "PMI", "VIX", "ETF", "PCE", "PPI", "ISM", "ADP", "IPO"}
    tickers = list(dict.fromkeys(tw + us))
    return [t for t in tickers if t not in skip]


# ── Blog ──────────────────────────────────────────────────────────────────────

async def collect_blog_links(context: BrowserContext, cutoff: datetime) -> list[dict]:
    """Collect all blog article links via infinite scroll."""
    page = await context.new_page()
    await page.goto("https://www.macromicro.me/blog", wait_until="networkidle")
    await page.wait_for_timeout(2000)

    prev_count = 0
    for i in range(20):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        dates = await page.eval_on_selector_all(
            "time[datetime]", "els => els.map(el => el.getAttribute('datetime'))"
        )
        unique = list(dict.fromkeys(dates))
        if len(unique) == prev_count and i > 2:
            break
        prev_count = len(unique)
        if unique and unique[-1] < cutoff.strftime("%Y-%m-%d"):
            break

    # Collect all unique blog links (excluding tag/index pages)
    articles = await page.eval_on_selector_all(
        "a[href*='/blog/']",
        r"""els => {
            const seen = new Set();
            const result = [];
            for (const el of els) {
                const href = el.href;
                if (seen.has(href)) continue;
                if (href.includes('/tag/') || href.match(/\/blog\/?$/)) continue;
                seen.add(href);
                // Try to find the closest time element
                const card = el.closest('article') || el.closest('[class*="card"]') || el.closest('li') || el.parentElement;
                const timeEl = card ? card.querySelector('time[datetime]') : null;
                const titleEl = card ? (card.querySelector('h2,h3,h4,[class*="title"]') || el) : el;
                result.push({
                    href,
                    title: titleEl.innerText.trim().replace(/\n.*$/s, '').slice(0, 100),
                    date: timeEl ? timeEl.getAttribute('datetime') : ''
                });
            }
            return result;
        }"""
    )

    await page.close()

    valid = []
    for a in articles:
        if not a["href"] or not a["title"]:
            continue
        dt = parse_date(a["date"]) if a["date"] else None
        if dt is None or dt >= cutoff:
            valid.append({**a, "published_at": dt, "report_type": "blog"})
    return valid


async def fetch_blog_article(context: BrowserContext, url: str, title: str,
                             published_at: Optional[datetime]) -> Optional[dict]:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # Check for locked content
        locked = await page.locator("text=解鎖訂閱報告").count()

        content_el = page.locator("article").first
        if await content_el.count() == 0:
            return None
        content = await content_el.inner_text()

        # If locked with short content, skip
        if locked and len(content) < 500:
            print(f"  [LOCKED] {url}")
            return None

        page_title = await page.title()
        clean_title = re.sub(r'\s*\|.*$', '', page_title).strip() or title

        if published_at is None:
            time_el = page.locator("time[datetime]").first
            if await time_el.count() > 0:
                dt_str = await time_el.get_attribute("datetime")
                published_at = parse_date(dt_str) if dt_str else None

        return {
            "source": SOURCE,
            "url": url,
            "title": clean_title,
            "author": "財經M平方",
            "published_at": published_at,
            "content": content.strip(),
            "tickers": extract_tickers(content),
        }
    except Exception as e:
        print(f"  [ERROR] {url}: {e}")
        return None
    finally:
        await page.close()


# ── Monthly Reports & EDM ─────────────────────────────────────────────────────

async def collect_report_links(context: BrowserContext, cutoff: datetime) -> list[dict]:
    """Collect monthly report + EDM quick report links."""
    page = await context.new_page()
    await page.goto("https://www.macromicro.me/mails/monthly_report", wait_until="networkidle")
    await page.wait_for_timeout(2000)

    # Scroll to load all reports
    prev_count = 0
    for i in range(15):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        links = await page.eval_on_selector_all(
            "a[href*='/mails/']", "els => els.map(el => el.href)"
        )
        unique = list(dict.fromkeys(links))
        if len(unique) == prev_count and i > 2:
            break
        prev_count = len(unique)

    reports = await page.eval_on_selector_all(
        "a[href*='/mails/monthly_report_v2/'], a[href*='/mails/edm/']",
        r"""els => {
            const seen = new Set();
            return els.map(el => {
                const href = el.href;
                if (seen.has(href)) return null;
                seen.add(href);
                const card = el.closest('[class*="item"]') || el.closest('li') || el.parentElement;
                const dateEl = card ? card.querySelector('[class*="date"]') : null;
                return {
                    href,
                    title: el.innerText.trim().slice(0, 100),
                    date: dateEl ? dateEl.innerText.trim() : ''
                };
            }).filter(Boolean);
        }"""
    )

    await page.close()

    valid = []
    for r in reports:
        if not r["href"] or not r["title"]:
            continue
        dt = parse_date(r["date"]) if r["date"] else None
        if dt is None or dt >= cutoff:
            report_type = "monthly_report" if "monthly_report_v2" in r["href"] else "edm"
            valid.append({**r, "published_at": dt, "report_type": report_type})
    return valid


async def fetch_report(context: BrowserContext, url: str, title: str,
                       published_at: Optional[datetime], report_type: str) -> Optional[dict]:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Different selectors per report type
        if report_type == "monthly_report":
            sel = "[class*='report']"
        else:
            sel = "article"

        content_el = page.locator(sel).first
        if await content_el.count() == 0:
            content_el = page.locator("main, body").first

        content = await content_el.inner_text()

        # Extract date from content if missing
        if published_at is None:
            m = re.search(r'(\d{4}-\d{2}-\d{2})', content)
            if m:
                published_at = parse_date(m.group(1))

        # Extract title from content (first line often contains it)
        if not title or title == "MM獨家報告":
            first_line = content.strip().split('\n')[0]
            title = first_line[:100]

        page_title = await page.title()
        clean_title = re.sub(r'\s*\|.*$', '', page_title).strip() or title

        return {
            "source": SOURCE,
            "url": url,
            "title": clean_title,
            "author": "財經M平方",
            "published_at": published_at,
            "content": content.strip(),
            "tickers": extract_tickers(content),
        }
    except Exception as e:
        print(f"  [ERROR] {url}: {e}")
        return None
    finally:
        await page.close()


# ── Main ──────────────────────────────────────────────────────────────────────

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

        # Collect all links
        print("Collecting blog articles...")
        blog_links = await collect_blog_links(context, cutoff)
        print(f"  Found {len(blog_links)} blog articles")

        print("Collecting monthly reports & EDM...")
        report_links = await collect_report_links(context, cutoff)
        print(f"  Found {len(report_links)} reports")

        all_links = blog_links + report_links

        # Filter already-crawled
        to_fetch = []
        for item in all_links:
            exists = await conn.fetchval("SELECT 1 FROM articles WHERE url=$1", item["href"])
            if not exists:
                to_fetch.append(item)
        print(f"\n{len(to_fetch)} new items to fetch (skipping {len(all_links)-len(to_fetch)} existing)")

        # Fetch with concurrency limit
        sem = asyncio.Semaphore(CONCURRENCY)

        async def bounded_fetch(item):
            async with sem:
                rt = item["report_type"]
                if rt == "blog":
                    result = await fetch_blog_article(context, item["href"], item["title"], item.get("published_at"))
                else:
                    result = await fetch_report(context, item["href"], item["title"], item.get("published_at"), rt)
                if result:
                    print(f"  [{rt}] {result['title'][:55]} ({len(result['content'])} chars)")
                return result

        results = await asyncio.gather(*[bounded_fetch(i) for i in to_fetch])
        fetched = [r for r in results if r is not None]

        new_count = await upsert_articles(conn, fetched)
        print(f"\nDone. {new_count} new articles saved.")

        await browser.close()

    await conn.close()
    return new_count


if __name__ == "__main__":
    import sys
    asyncio.run(crawl(incremental="--incremental" in sys.argv))
