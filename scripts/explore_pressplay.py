#!/usr/bin/env python3
"""Explore PressPlay article list structure via CDP."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
BASE_URL = "https://www.pressplay.cc/member/learning/projects/EFB905DAF7B44F479552E5F5D955A137/articles"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        print("=== PressPlay Index ===")
        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)
        print(f"URL: {page.url}")
        print(f"Title: {await page.title()}")

        # Scroll a few times
        for _ in range(4):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)

        # Article links
        all_hrefs = await page.eval_on_selector_all(
            "a[href]",
            "els => [...new Set(els.map(el=>el.href).filter(h=>h.includes('pressplay.cc')))]"
        )
        print(f"\nAll pressplay.cc hrefs ({len(all_hrefs)}):")
        for h in sorted(set(all_hrefs))[:40]:
            print(f"  {h}")

        # Specific article-like links
        art_links = await page.eval_on_selector_all(
            "a[href*='/article'], a[href*='/post'], a[href*='/content'], a[href*='/learning']",
            "els => [...new Set(els.map(el=>({href:el.href, text:el.innerText.trim().slice(0,70)})))].filter(e=>e.text)"
        )
        print(f"\nArticle-like links ({len(art_links)}):")
        for l in art_links[:15]:
            print(f"  {l['text']!r} -> {l['href']}")

        # Date elements
        print("\nDate elements:")
        for sel in ["time[datetime]", "time", "[class*='date']", "[class*='time']", "[class*='publish']"]:
            count = await page.locator(sel).count()
            if count > 0:
                texts = []
                for i in range(min(3, count)):
                    t = await page.locator(sel).nth(i).inner_text()
                    dt = await page.locator(sel).nth(i).get_attribute("datetime")
                    if t.strip() or dt:
                        texts.append(f"{t.strip()[:25]!r}|{dt!r}")
                if texts:
                    print(f"  '{sel}' ({count}): {'; '.join(texts)}")

        # Pagination
        print("\nPagination:")
        for sel in ["[class*='page']", "a[rel='next']", "button[class*='more']", "[class*='load']"]:
            count = await page.locator(sel).count()
            if count > 0:
                t = await page.locator(sel).first.inner_text()
                if t.strip():
                    print(f"  '{sel}' ({count}): {t.strip()[:60]!r}")

        # Body text snippet
        body = await page.locator("body").inner_text()
        print(f"\nBody length: {len(body)}")
        print(f"Body snippet: {body[:500]!r}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
