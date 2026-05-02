#!/usr/bin/env python3
"""Use PressPlay API directly to get full article list."""
import asyncio
import json
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
FIRST_ART_ID = "3C9E58847448C89CB0C68C4856808EE3"  # known recent article


async def api_get(page, url):
    """Make authenticated GET request via page.evaluate fetch."""
    result = await page.evaluate(f"""async () => {{
        const r = await fetch({url!r}, {{credentials: 'include'}});
        return await r.text();
    }}""")
    return result


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]
        page = await context.new_page()

        # Load the articles page to establish session
        await page.goto(
            f"https://www.pressplay.cc/project/{PROJECT_ID}/articles/{FIRST_ART_ID}",
            wait_until="networkidle", timeout=45000
        )
        await page.wait_for_timeout(2000)

        # --- 1. Get search filter (date range) ---
        print("=== Search Filter ===")
        sf = await api_get(page, f"https://og-web.pressplay.cc/project/{PROJECT_ID}/search_filter")
        sf_data = json.loads(sf)
        print(json.dumps(sf_data.get('data', {}), indent=2, ensure_ascii=False)[:800])

        # --- 2. Get directory v2 (full article list) ---
        print("\n=== Directory v2 ===")
        dir_url = f"https://og-web.pressplay.cc/project/{PROJECT_ID}/directory/v2?timeline_key={FIRST_ART_ID}"
        dir_raw = await api_get(page, dir_url)
        dir_data = json.loads(dir_raw)

        # Parse chapters and articles
        chapters = dir_data.get('data', {}).get('book_info', {}).get('chapters', [])
        print(f"Chapters: {len(chapters)}")
        total_articles = 0
        for ch in chapters[:3]:
            print(f"  Chapter: {ch.get('chapter_title','')!r} type={ch.get('chapter_type')}")
            sub = ch.get('sub_chapter_list') or ch.get('articles') or []
            print(f"    Sub-items: {len(sub)}")
            if sub:
                first = sub[0]
                print(f"    First item keys: {list(first.keys())[:10]}")
                print(f"    First item sample: {json.dumps(first, ensure_ascii=False)[:300]}")
            total_articles += len(sub)

        print(f"\nTotal articles across all chapters: {total_articles}")

        # --- 3. Try search API with date filter ---
        print("\n=== Search API (recent articles) ===")
        search_url = f"https://og-web.pressplay.cc/project/{PROJECT_ID}/articles?page=1&count=50&sort=latest"
        search_raw = await api_get(page, search_url)
        print(f"Search response (500 chars): {search_raw[:500]}")

        # --- 4. Try timeline API ---
        print("\n=== Timeline / Articles list API ===")
        for url_template in [
            f"https://og-web.pressplay.cc/project/{PROJECT_ID}/timeline?page=1&count=30",
            f"https://og-web.pressplay.cc/project/{PROJECT_ID}/articles/list?page=1&count=30",
            f"https://og-web.pressplay.cc/project/{PROJECT_ID}/contents?page=1&count=30&sort=latest",
        ]:
            raw = await api_get(page, url_template)
            if raw and raw.startswith('{'):
                data = json.loads(raw)
                print(f"\n  URL: {url_template}")
                print(f"  Response: {json.dumps(data, ensure_ascii=False)[:400]}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
