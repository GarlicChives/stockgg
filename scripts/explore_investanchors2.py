#!/usr/bin/env python3
"""Explore pagination and single article structure on InvestAnchors."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
INDEX_URL = "https://investanchors.com/user/vip_contents/investanchors_index"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        # --- 1. Check index page for pagination ---
        index_page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "investanchors_index" in pg.url:
                    index_page = pg
                    break
        if index_page is None:
            index_page = await context.new_page()
            await index_page.goto(INDEX_URL, wait_until="networkidle")

        await index_page.bring_to_front()

        # Scroll to bottom to trigger lazy load
        print("=== Scrolling to load all articles ===")
        prev_count = 0
        for i in range(10):
            await index_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await index_page.wait_for_timeout(1500)
            links = await index_page.eval_on_selector_all(
                "a[href*='/vip_contents/']",
                "els => els.map(el => el.href)"
            )
            # Filter out the index page itself
            article_links = [l for l in links if "index" not in l]
            unique_links = list(dict.fromkeys(article_links))
            print(f"  Scroll {i+1}: {len(unique_links)} unique articles")
            if len(unique_links) == prev_count and i > 1:
                print("  No more articles loading, stopping.")
                break
            prev_count = len(unique_links)

        print(f"\nTotal unique articles found: {len(unique_links)}")
        for i, url in enumerate(unique_links[:5]):
            print(f"  {i+1}. {url}")

        # Check for pagination buttons
        print("\n=== Pagination Elements ===")
        pagination = await index_page.locator("[class*='page'], [class*='pagination'], .more, button").all()
        for el in pagination[:10]:
            text = await el.inner_text()
            tag = await el.evaluate("el => el.tagName")
            print(f"  <{tag}> {text.strip()!r}")

        # --- 2. Explore a single article ---
        if unique_links:
            article_url = unique_links[0]
            print(f"\n=== Single Article: {article_url} ===")
            article_page = await context.new_page()
            await article_page.goto(article_url, wait_until="networkidle")
            await article_page.wait_for_timeout(2000)

            title = await article_page.title()
            print(f"Title: {title}")

            # Try common content selectors
            for sel in ["article", ".content", ".article-body", "[class*='content']", "main", ".post-body"]:
                count = await article_page.locator(sel).count()
                if count > 0:
                    text = await article_page.locator(sel).first.inner_text()
                    print(f"\n  Selector '{sel}' ({count} matches), first 500 chars:")
                    print(f"  {text[:500]!r}")
                    break

            # Get published date
            for date_sel in ["time", "[class*='date']", "[class*='time']", ".published"]:
                count = await article_page.locator(date_sel).count()
                if count > 0:
                    text = await article_page.locator(date_sel).first.inner_text()
                    attr = await article_page.locator(date_sel).first.get_attribute("datetime")
                    print(f"\n  Date selector '{date_sel}': text={text.strip()!r}, datetime={attr!r}")

            await article_page.close()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
