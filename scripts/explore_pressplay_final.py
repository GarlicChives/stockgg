#!/usr/bin/env python3
"""Get ALL article URLs from PressPlay via timelines/v2 API pagination."""
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

        captured = []
        async def handle_response(resp):
            if 'og-web.pressplay.cc' in resp.url and 'timelines' in resp.url:
                try:
                    body = await resp.text()
                    captured.append({'url': resp.url, 'body': body})
                except:
                    pass
        page.on("response", handle_response)

        await page.goto(ARTICLES_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        # Find initial timelines API URL
        base_api_url = None
        for c in captured:
            if 'timelines' in c['url']:
                print(f"Initial timelines API: {c['url']}")
                data = json.loads(c['body'])
                lst = data.get('data', {}).get('list', [])
                print(f"Articles: {len(lst)}")
                for a in lst[:5]:
                    print(f"  [{a.get('release_time','')[:10]}] {a.get('timeline_title','')[:60]!r}")
                base_api_url = c['url']
                break

        if not base_api_url:
            print("No timelines API found, checking all captured:")
            for c in captured:
                print(f"  {c['url'][:100]}")
            await browser.close()
            return

        # Now try pagination: change page=1 to page=2,3,4
        import re
        print(f"\n=== Testing pagination on timelines API ===")

        all_articles = []
        for pg in range(1, 5):
            paginated_url = re.sub(r'page=\d+', f'page={pg}', base_api_url)
            if 'page=' not in base_api_url:
                paginated_url = base_api_url + (f"&page={pg}" if '?' in base_api_url else f"?page={pg}")

            raw = await page.evaluate(f"""async () => {{
                const r = await fetch({paginated_url!r}, {{credentials: 'include'}});
                return await r.text();
            }}""")

            try:
                data = json.loads(raw)
                lst = data.get('data', {}).get('list', [])
                print(f"\nPage {pg}: {len(lst)} articles (URL: {paginated_url[:80]})")
                for a in lst:
                    key = a.get('timeline_key', '')
                    title = a.get('timeline_title', '') or a.get('title', '')
                    release = a.get('release_time', '')[:10]
                    if key:
                        all_articles.append({'key': key, 'title': title, 'release': release})
                        print(f"  [{release}] {title[:60]!r}")
            except Exception as e:
                print(f"Page {pg}: error — {raw[:100]}")
                break

        print(f"\n=== TOTAL: {len(all_articles)} articles collected ===")
        # Save to file for use by crawler
        with open('/tmp/pressplay_articles.json', 'w', encoding='utf-8') as f:
            json.dump(all_articles, f, ensure_ascii=False, indent=2)
        print("Saved to /tmp/pressplay_articles.json")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
