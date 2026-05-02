#!/usr/bin/env python3
"""Parse PressPlay directory/v2 full response — extract all article IDs and dates."""
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

        raw_body = None

        async def handle_response(resp):
            nonlocal raw_body
            if "directory/v2" in resp.url:
                try:
                    raw_body = await resp.text()
                except:
                    pass

        page.on("response", handle_response)

        await page.goto(
            f"https://www.pressplay.cc/project/{PROJECT_ID}/articles/{FIRST_ART_ID}",
            wait_until="networkidle", timeout=45000
        )
        await page.wait_for_timeout(3000)

        if not raw_body:
            print("No directory/v2 response captured")
            return

        data = json.loads(raw_body)
        d = data.get('data', {})

        # chapter_timelines: articles listed in sidebar
        ct = d.get('chapter_timelines', [])
        print(f"chapter_timelines count: {len(ct)}")
        if ct:
            print(f"First item keys: {list(ct[0].keys())}")
            print(f"First item: {json.dumps(ct[0], ensure_ascii=False)[:500]}")
            print(f"\nLast item: {json.dumps(ct[-1], ensure_ascii=False)[:300]}")

        # chapters structure
        chapters = d.get('book_info', {}).get('chapters', [])
        print(f"\nChapters count: {len(chapters)}")
        for i, ch in enumerate(chapters[:3]):
            print(f"\nChapter {i}: {json.dumps(ch, ensure_ascii=False)[:400]}")

        # Save full response for analysis
        with open("/tmp/pressplay_dir.json", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("\nFull response saved to /tmp/pressplay_dir.json")

        # Extract all article timeline keys
        print("\n=== All articles in chapter_timelines ===")
        for art in ct:
            key = art.get('timeline_key', '')
            title = art.get('title', '')
            release = art.get('release_time', '') or art.get('created_at', '')
            print(f"  [{release[:10]}] {title[:60]!r}  key={key[:20]}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
