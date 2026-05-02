#!/usr/bin/env python3
"""Explore PressPlay directory links and article content."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
DIR_URL = f"https://www.pressplay.cc/member/learning/projects/{PROJECT_ID}/directory"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        # --- 1. Directory: collect all article links ---
        print("=== Directory: article links ===")
        page = await context.new_page()
        await page.goto(DIR_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        # Scroll to load all
        prev_len = 0
        for i in range(15):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)
            links = await page.eval_on_selector_all(
                f"a[href*='/project/{PROJECT_ID}/articles/']",
                "els => [...new Set(els.map(el => el.href))]"
            )
            if len(links) == prev_len and i > 2:
                break
            prev_len = len(links)

        art_links = await page.eval_on_selector_all(
            f"a[href*='/project/{PROJECT_ID}/articles/']",
            """els => {
                const seen = new Set();
                return els.map(el => {
                    if (seen.has(el.href)) return null;
                    seen.add(el.href);
                    const card = el.closest('li') || el.closest('[class*="item"]') || el.closest('[class*="card"]') || el.parentElement;
                    return {
                        href: el.href,
                        text: el.innerText.trim().slice(0, 80)
                    };
                }).filter(Boolean);
            }"""
        )
        print(f"Total article links found: {len(art_links)}")
        for l in art_links[:15]:
            print(f"  {l['text']!r}")
            print(f"    {l['href']}")

        # Get full body text to see if dates are embedded
        body = await page.locator("body").inner_text()
        print(f"\nBody text length: {len(body)}")
        # Find date patterns in body
        import re
        dates = re.findall(r'\d{4}/\d{2}/\d{2}|\d{4}-\d{2}-\d{2}', body)
        print(f"Date patterns in body: {dates[:10]}")

        await page.close()

        # --- 2. Single article content ---
        if art_links:
            art_url = art_links[0]["href"]
            print(f"\n=== Article: {art_url} ===")
            art_page = await context.new_page()
            await art_page.goto(art_url, wait_until="networkidle", timeout=45000)
            await art_page.wait_for_timeout(2000)

            # Scroll to load
            for _ in range(4):
                await art_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await art_page.wait_for_timeout(600)

            print(f"Title: {await art_page.title()}")

            # Find content
            for sel in ["article", "main", "[class*='content']", "[class*='article']",
                        "[class*='post']", "[class*='body']", "[class*='editor']",
                        "[class*='lesson']", "[class*='chapter']"]:
                count = await art_page.locator(sel).count()
                if count > 0:
                    text = await art_page.locator(sel).first.inner_text()
                    if len(text) > 300:
                        print(f"\n  '{sel}' len={len(text)}: {text[50:500]!r}")
                        break

            # Date
            body_text = await art_page.locator("body").inner_text()
            dates_in_art = re.findall(r'\d{4}[/-]\d{1,2}[/-]\d{1,2}', body_text)
            print(f"\n  Dates in article: {dates_in_art[:5]}")

            # Check for time elements
            for sel in ["time[datetime]", "time", "[class*='date']", "[class*='publish']",
                        "[class*='created']", "meta[property='article:published_time']"]:
                count = await art_page.locator(sel).count()
                if count > 0:
                    t = await art_page.locator(sel).first.inner_text()
                    dt = await art_page.locator(sel).first.get_attribute("datetime") or \
                         await art_page.locator(sel).first.get_attribute("content")
                    if t.strip() or dt:
                        print(f"  Date '{sel}': {t.strip()[:30]!r} | {dt!r}")
                        break

            print(f"  Body total len: {len(body_text)}")
            await art_page.close()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
