#!/usr/bin/env python3
"""PressPlay 財經捕手 crawler — uses timelines/v2 API via CDP session."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext

from src.utils.browser import connect_browser
from src.utils.refine import refine_and_store

load_dotenv()

SOURCE = "pressplay"
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
ARTICLES_URL = f"https://www.pressplay.cc/member/learning/projects/{PROJECT_ID}/articles"
ARTICLE_BASE = f"https://www.pressplay.cc/project/{PROJECT_ID}/articles"
LOOKBACK_DAYS = 180
CONCURRENCY = 2
MAX_PAGES = 10  # safety cap; stop early if list returns empty


def extract_tickers(text: str) -> list[str]:
    tw = re.findall(r'\((\d{4,5})\)', text)
    us = re.findall(r'\(([A-Z]{2,5})\)', text)
    skip = {"QoQ", "YoY", "EPS", "CEO", "AI", "US", "TW", "GDP", "CPI", "PMI", "VIX",
            "ETF", "PCE", "PPI", "IPO", "GAAP", "QE", "USMCA", "FED", "HBM", "DDR",
            "GPU", "CPU", "FCF", "RPO", "ACV", "TAM", "GMV"}
    all_t = list(dict.fromkeys(tw + us))
    return [t for t in all_t if t not in skip and len(t) >= 2]


def parse_release_time(s: str) -> Optional[datetime]:
    """Parse ISO-style release_time from timelines/v2 API (e.g. '2026-04-30 21:28:00')."""
    s = s.strip()
    # Slice to the actual expected length of each format, not the format string length
    for fmt, n in [("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)]:
        try:
            return datetime.strptime(s[:n], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def collect_article_keys(page, cutoff: datetime) -> list[dict]:
    """
    Load /articles page, intercept the timelines/v2 request (method + URL + body),
    then replay with page=1..MAX_PAGES via fetch() in the browser session.
    Returns list of {key, title, release} dicts within the cutoff window.
    """
    captured_req = []

    async def handle_request(req):
        if 'og-web.pressplay.cc' in req.url and 'timelines' in req.url:
            captured_req.append({
                'url': req.url,
                'method': req.method,
                'post_data': req.post_data,
                'headers': dict(req.headers),
            })

    page.on("request", handle_request)
    await page.goto(ARTICLES_URL, wait_until="networkidle", timeout=45000)
    await page.wait_for_timeout(2000)
    page.remove_listener("request", handle_request)

    if not captured_req:
        print("ERROR: no timelines API request captured on /articles page load")
        return []

    req = captured_req[0]
    api_url = req['url']
    method = req['method'].upper()
    post_data = req['post_data'] or ''
    print(f"Timelines API: [{method}] {api_url[:100]}")
    if post_data:
        print(f"  POST body: {post_data[:200]}")

    articles = []
    hit_cutoff = False

    for pg in range(1, MAX_PAGES + 1):
        # Modify page number in POST body (JSON) or query string
        if method == 'POST' and post_data:
            try:
                body_obj = json.loads(post_data)
                body_obj['page'] = pg
                body_str = json.dumps(body_obj)
            except Exception:
                if re.search(r'"page"\s*:\s*\d+', post_data):
                    body_str = re.sub(r'"page"\s*:\s*\d+', f'"page":{pg}', post_data)
                else:
                    body_str = post_data
            # Pass original headers (excluding pseudo-headers and content-length)
            skip_hdrs = {'content-length', 'host', 'origin'}
            safe_headers = {k: v for k, v in req['headers'].items()
                           if not k.startswith(':') and k.lower() not in skip_hdrs}
            raw = await page.evaluate("""async ([url, body, hdrs]) => {
                try {
                    const r = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: Object.assign({'content-type': 'application/json'}, hdrs),
                        body: body
                    });
                    return await r.text();
                } catch(e) { return '{"error":"' + e.message + '"}'; }
            }""", [api_url, body_str, safe_headers])
        else:
            # GET — modify page= in query string
            url_no_page = re.sub(r'[&?]page=\d+', '', api_url)
            sep = '&' if '?' in url_no_page else '?'
            paged_url = f"{url_no_page}{sep}page={pg}"
            raw = await page.evaluate(r"""async (url) => {
                try {
                    const r = await fetch(url, {credentials: 'include'});
                    return await r.text();
                } catch(e) { return '{"error":"' + e.message + '"}'; }
            }""", paged_url)

        try:
            data = json.loads(raw)
        except Exception:
            print(f"  Page {pg}: JSON parse error — {raw[:120]}")
            break

        lst = data.get('data', {}).get('list', [])
        print(f"  Page {pg}: {len(lst)} articles")

        if not lst:
            break

        for a in lst:
            key = a.get('timeline_key', '')
            title = a.get('timeline_title', '') or a.get('title', '')
            release_str = a.get('release_time', '') or a.get('created_at', '')
            release_dt = parse_release_time(release_str) if release_str else None

            if release_dt and release_dt < cutoff:
                hit_cutoff = True
                continue

            if key:
                articles.append({
                    'key': key,
                    'title': title,
                    'release': release_dt,
                })

        if hit_cutoff:
            break

    print(f"Collected {len(articles)} articles within 6-month window")
    return articles


async def fetch_article(context: BrowserContext, key: str, title: str,
                        release: Optional[datetime]) -> Optional[dict]:
    """Navigate directly to article URL and extract content. No sidebar navigation."""
    url = f"{ARTICLE_BASE}/{key}"
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1500)

        # Scroll to reveal lazy-loaded content
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(600)

        # Extract content (avoid sidebar by targeting article-specific containers)
        content = ""
        for sel in [
            "[class*='timeline-content']",
            "[class*='article-content']",
            "[class*='post-content']",
            "article",
            "main",
        ]:
            el = page.locator(sel).first
            if await el.count() > 0:
                text = (await el.inner_text()).strip()
                if len(text) > 200:
                    content = text
                    break

        if not content:
            print(f"  [SKIP] {url}: no content found")
            return None

        # Use page title if no title supplied from API
        if not title:
            page_title = await page.title()
            title = re.sub(r'\s*[-|].*PressPlay.*$', '', page_title).strip()

        published_at = release

        # Fallback: try to parse date from content
        if published_at is None:
            m = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', content[:300])
            if m:
                raw_date = m.group(1).replace('/', '-')
                try:
                    published_at = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

        return {
            "source": SOURCE,
            "url": url,
            "title": title,
            "author": "財經捕手",
            "published_at": published_at,
            "content": content,
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
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

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
        page = await context.new_page()

        print("Collecting article list from timelines/v2 API...")
        article_keys = await collect_article_keys(page, cutoff)
        await page.close()

        # Filter already-crawled
        to_fetch = []
        for a in article_keys:
            url = f"{ARTICLE_BASE}/{a['key']}"
            exists = await conn.fetchval("SELECT 1 FROM articles WHERE url=$1", url)
            if not exists:
                to_fetch.append(a)
        print(f"{len(to_fetch)} new articles to fetch (skipping {len(article_keys)-len(to_fetch)} existing)")

        sem = asyncio.Semaphore(CONCURRENCY)
        fetched = []

        async def bounded_fetch(a):
            async with sem:
                result = await fetch_article(context, a['key'], a['title'], a['release'])
                if result:
                    date_str = str(result['published_at'])[:10] if result['published_at'] else '????-??-??'
                    print(f"  [{date_str}] {result['title'][:60]} ({len(result['content'])} chars)")
                return result

        results = await asyncio.gather(*[bounded_fetch(a) for a in to_fetch])
        fetched = [r for r in results if r is not None]

        new_count = await upsert_articles(conn, fetched)
        skipped = len(to_fetch) - len(fetched)
        print(f"\nDone. {new_count} new articles saved ({skipped} failed/empty).")

        await browser.close()

    await conn.close()
    return new_count


if __name__ == "__main__":
    import sys
    asyncio.run(crawl(incremental="--incremental" in sys.argv))
