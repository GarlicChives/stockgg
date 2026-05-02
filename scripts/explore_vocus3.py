#!/usr/bin/env python3
"""Explore Vocus article content depth and paywall behaviour."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        art_url = "https://vocus.cc/article/69e15c03fd89780001eb324f"
        page = await context.new_page()
        await page.goto(art_url, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Scroll to trigger lazy content
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(800)

        # Try all selectors and report lengths
        print("=== Content selector lengths ===")
        selectors = ["article", "main", "body", "[class*='content']", "[class*='article']",
                     "[class*='editor']", "[class*='post']", "[class*='prose']",
                     ".draft-editor-root", "[class*='draft']"]
        for sel in selectors:
            count = await page.locator(sel).count()
            if count > 0:
                for i in range(min(count, 3)):
                    text = await page.locator(sel).nth(i).inner_text()
                    if len(text) > 100:
                        print(f"  '{sel}'[{i}] len={len(text)}")

        # Get full article element HTML structure
        print("\n=== Article element detail ===")
        count = await page.locator("article").count()
        print(f"article count: {count}")
        if count > 0:
            text = await page.locator("article").first.inner_text()
            print(f"article text len: {len(text)}")
            print(f"First 200: {text[:200]!r}")
            print(f"Last 200: {text[-200:]!r}")

        # Check for paywall/subscription prompt
        print("\n=== Paywall check ===")
        for kw in ["訂閱", "解鎖", "付費", "加入", "premium", "unlock", "subscribe"]:
            count = await page.locator(f"text='{kw}'").count()
            if count > 0:
                print(f"  Found '{kw}' ({count} times)")

        # Check full page text length
        body_text = await page.locator("body").inner_text()
        print(f"\nbody total text length: {len(body_text)}")

        # Get the HTML of article to find the real content container
        html = await page.content()
        # Find where article content starts
        for marker in ["editor", "content", "prose", "article-body", "post-content"]:
            idx = html.lower().find(f'class="{marker}')
            if idx == -1:
                idx = html.lower().find(f"class='{marker}")
            if idx > -1:
                snippet = html[idx:idx+200]
                print(f"\n  Found class marker '{marker}' at {idx}: {snippet[:100]!r}")

        await page.close()

        # Also check index page date structure
        print("\n=== Index date structure ===")
        page = await context.new_page()
        await page.goto("https://vocus.cc/salon/ChivesKing", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Get first article card HTML
        first_card_html = await page.eval_on_selector(
            "a[href*='/article/']",
            """el => {
                const card = el.closest('li') || el.closest('[class*="card"]') || el.closest('[class*="item"]') || el.parentElement?.parentElement;
                return card ? card.outerHTML.slice(0, 500) : 'NO CARD';
            }"""
        )
        print(f"First card HTML snippet:\n{first_card_html}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
