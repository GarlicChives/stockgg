#!/usr/bin/env python3
"""Intercept PressPlay directory/v2 API response to get full article list."""
import asyncio
import json
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
FIRST_ART_ID = "3C9E58847448C89CB0C68C4856808EE3"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]
        page = await context.new_page()

        captured = {}

        async def handle_response(resp):
            url = resp.url
            if "directory/v2" in url or "search_filter" in url:
                try:
                    body = await resp.text()
                    captured[url] = body
                    print(f"  Captured: {url[:80]}")
                except:
                    pass

        page.on("response", handle_response)

        print("Loading article page to trigger API calls...")
        await page.goto(
            f"https://www.pressplay.cc/project/{PROJECT_ID}/articles/{FIRST_ART_ID}",
            wait_until="networkidle", timeout=45000
        )
        await page.wait_for_timeout(3000)

        for url, body in captured.items():
            print(f"\n=== {url[:100]} ===")
            try:
                data = json.loads(body)
                # Pretty print key structure
                def summarize(obj, depth=0, max_depth=3):
                    if depth > max_depth:
                        return "..."
                    if isinstance(obj, dict):
                        return {k: summarize(v, depth+1) for k, v in list(obj.items())[:5]}
                    elif isinstance(obj, list):
                        return [summarize(obj[0], depth+1)] + [f"... ({len(obj)-1} more)"] if obj else []
                    else:
                        return str(obj)[:80]
                print(json.dumps(summarize(data), indent=2, ensure_ascii=False))

                # If directory, extract article IDs
                if "directory" in url:
                    chapters = data.get('data', {}).get('book_info', {}).get('chapters', [])
                    print(f"\nChapters: {len(chapters)}")
                    all_articles = []
                    for ch in chapters:
                        sub = ch.get('sub_chapter_list', [])
                        for s in sub:
                            arts = s.get('articles', s.get('article_list', []))
                            all_articles.extend(arts)
                        # Also check direct articles
                        arts = ch.get('articles', ch.get('article_list', []))
                        all_articles.extend(arts)
                    print(f"Total articles: {len(all_articles)}")
                    if all_articles:
                        print(f"First article keys: {list(all_articles[0].keys())}")
                        print(f"First article: {json.dumps(all_articles[0], ensure_ascii=False)[:400]}")
            except json.JSONDecodeError:
                print(f"Not JSON: {body[:200]}")

        # Also try scrolling sidebar to trigger pagination
        print("\n=== Scrolling sidebar to load more ===")
        captured2 = {}

        async def handle_response2(resp):
            url = resp.url
            if any(x in url for x in ['timeline', 'articles', 'chapter', 'lesson']):
                if 'og-web' in url or 'api-web' in url:
                    try:
                        body = await resp.text()
                        captured2[url] = body[:400]
                    except:
                        pass

        page.on("response", handle_response2)

        # Scroll the sidebar container
        await page.evaluate("""() => {
            const el = document.querySelector('.os-viewport');
            if (el) {
                for (let i = 0; i < 30; i++) {
                    setTimeout(() => { el.scrollTop += 300; }, i * 200);
                }
            }
        }""")
        await page.wait_for_timeout(8000)

        print(f"New API calls during scroll ({len(captured2)}):")
        for url, body in captured2.items():
            print(f"  {url[:100]}")
            print(f"    {body[:200]}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
