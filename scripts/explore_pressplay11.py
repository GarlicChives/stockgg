#!/usr/bin/env python3
"""Find PressPlay timelines/v2 pagination and date filter API."""
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
            if 'og-web.pressplay.cc' in url:
                try:
                    body = await resp.text()
                    if body.startswith('{'):
                        captured[url] = json.loads(body)
                except:
                    pass
        page.on("response", handle_response)

        await page.goto(ARTICLES_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        # Check the initial timelines/v2 response
        for url, data in list(captured.items()):
            if 'timelines/v2' in url:
                print(f"=== {url} ===")
                lst = data.get('data', {}).get('list', [])
                print(f"Articles in response: {len(lst)}")
                if lst:
                    print(f"First article keys: {list(lst[0].keys())}")
                    for a in lst[:5]:
                        print(f"  [{a.get('release_time','')[:10]}] {a.get('timeline_title','')[:60]!r}")
                        print(f"    key={a.get('timeline_key','')[:32]}")
                break

        captured.clear()

        # Scroll the article grid container to trigger load more
        print("\n=== Scrolling article grid ===")
        prev_links = 24
        for i in range(10):
            await page.evaluate("""() => {
                const el = document.querySelector('.module-articles-search-content') ||
                           document.querySelector('.module-articles-page');
                if (el) el.scrollTop += 800;
                window.scrollTo(0, document.body.scrollHeight);
            }""")
            await page.wait_for_timeout(1500)
            links = await page.eval_on_selector_all(
                f"a[href*='/project/{PROJECT_ID}/articles/']",
                "els => [...new Set(els.map(e=>e.href))].length"
            )
            print(f"  Scroll {i+1}: {links} article links")

            # Check for new API calls
            for url, data in list(captured.items()):
                if 'timelines' in url or 'articles' in url:
                    print(f"  New API call: {url[:80]}")
            captured.clear()

            if links == prev_links and i > 2:
                break
            prev_links = links

        # Try clicking "load more" or filter by date
        print("\n=== Filter elements ===")
        filter_el = page.locator("[class*='filter']").first
        if await filter_el.count() > 0:
            print(f"Filter text: {(await filter_el.inner_text())[:200]!r}")

        # Look for date filter or pagination
        captured.clear()
        print("\n=== Clicking into filter area ===")
        filter_links = await page.eval_on_selector_all(
            "[class*='filter'] a, [class*='filter'] button, [class*='category'] a",
            "els => els.map(el => ({text: el.innerText.trim().slice(0,40), href: el.href || ''}))"
        )
        print(f"Filter options: {filter_links[:10]}")

        # Try the 宏觀分析 category
        cat_link = page.locator("text='宏觀分析'").first
        if await cat_link.count() > 0:
            await cat_link.click()
            await page.wait_for_timeout(2000)
            print("\nAfter clicking 宏觀分析:")
            for url, data in captured.items():
                print(f"  API: {url[:100]}")
                lst = data.get('data', {}).get('list', [])
                print(f"  Articles: {len(lst)}")
                if lst:
                    for a in lst[:3]:
                        print(f"    [{a.get('release_time','')[:10]}] {a.get('timeline_title','')[:50]!r}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
