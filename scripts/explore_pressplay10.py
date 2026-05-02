#!/usr/bin/env python3
"""Find PressPlay article list API by intercepting filter actions on /articles page."""
import asyncio
import json
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
ARTICLES_URL = f"https://www.pressplay.cc/member/learning/projects/{PROJECT_ID}/articles"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]
        page = await context.new_page()

        captured = {}

        async def handle_response(resp):
            url = resp.url
            if ('og-web.pressplay.cc' in url or 'api-web.pressplay.cc' in url) and \
               any(x in url for x in ['timeline', 'article', 'project', 'content', 'chapter']):
                try:
                    body = await resp.text()
                    if body.startswith('{') or body.startswith('['):
                        captured[url] = body
                except:
                    pass

        page.on("response", handle_response)

        print("Loading /articles page...")
        await page.goto(ARTICLES_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        print(f"\nInitial API calls: {len(captured)}")
        for url, body in captured.items():
            if 'og-web' in url:
                print(f"  {url[:100]}")
                print(f"    {body[:200]}\n")

        captured.clear()

        # Try clicking search/filter button
        print("\n=== Looking for filter/search elements ===")
        for sel in ["[class*='filter']", "[class*='search']", "input[type='search']",
                    "input[type='text']", "[placeholder*='搜']", "[class*='sort']",
                    "button", "[class*='tab']"]:
            count = await page.locator(sel).count()
            if count > 0:
                t = await page.locator(sel).first.inner_text()
                if t.strip():
                    print(f"  '{sel}' ({count}): {t.strip()[:50]!r}")

        # Try scrolling the article list within the page
        print("\n=== Try scrolling article list container ===")
        # Find the specific container that shows articles
        container_info = await page.evaluate("""() => {
            // Look for the article list container
            const candidates = [];
            for (const el of document.querySelectorAll('[class*="article"], [class*="list"], [class*="content"]')) {
                const links = el.querySelectorAll('a[href*="/project/"]');
                if (links.length > 0) {
                    candidates.push({
                        tag: el.tagName,
                        cls: el.className.slice(0, 60),
                        linkCount: links.length,
                        scrollH: el.scrollHeight,
                        clientH: el.clientHeight
                    });
                }
            }
            return candidates;
        }""")
        print("Containers with article links:")
        for c in container_info[:10]:
            print(f"  <{c['tag']}> {c['cls']!r} links={c['linkCount']} scrollH={c['scrollH']}")

        # Try loading more via next_article chain (traverse backwards from latest)
        print("\n=== next_article chain traversal ===")
        FIRST_KEY = "3C9E58847448C89CB0C68C4856808EE3"
        captured_next = {}

        async def capture_next(resp):
            if 'next_article' in resp.url or 'previous' in resp.url:
                try:
                    body = await resp.text()
                    if body.startswith('{'):
                        captured_next[resp.url] = json.loads(body)
                except:
                    pass

        page.on("response", capture_next)

        # Navigate to article and trigger next_article
        await page.goto(
            f"https://www.pressplay.cc/project/{PROJECT_ID}/articles/{FIRST_KEY}",
            wait_until="networkidle", timeout=45000
        )
        await page.wait_for_timeout(3000)

        print(f"next_article calls captured: {len(captured_next)}")
        for url, data in captured_next.items():
            print(f"\n  {url[:100]}")
            next_arts = data.get('data', {}).get('next_article', [])
            last_arts = data.get('data', {}).get('last_article', [])
            print(f"  next_article count: {len(next_arts)}")
            print(f"  last_article count: {len(last_arts)}")
            if next_arts:
                a = next_arts[0]
                print(f"  next: key={a.get('timeline_key','')[:20]} title={a.get('timeline_title','')[:50]!r} release={a.get('release_time','')[:10]}")
            if last_arts:
                a = last_arts[0]
                print(f"  last: key={a.get('timeline_key','')[:20]} title={a.get('timeline_title','')[:50]!r} release={a.get('release_time','')[:10]}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
