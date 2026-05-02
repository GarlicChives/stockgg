#!/usr/bin/env python3
"""Explore InvestAnchors page structure via CDP connection to user's Chrome."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

URL = "https://investanchors.com/user/vip_contents/investanchors_index"
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        # Find existing tab or open new one
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "investanchors" in pg.url:
                    page = pg
                    break

        if page is None:
            page = await context.new_page()
            await page.goto(URL, wait_until="networkidle")
        else:
            print(f"Using existing tab: {page.url}")
            await page.bring_to_front()

        await page.wait_for_timeout(2000)

        # Get all article links
        print("\n=== Article Links ===")
        links = await page.eval_on_selector_all(
            "a[href*='/posts/'], a[href*='/articles/'], a[href*='vip_content']",
            "els => els.map(el => ({href: el.href, text: el.innerText.trim().slice(0, 80)}))"
        )
        for i, link in enumerate(links[:20]):
            print(f"{i+1}. {link['text']!r} -> {link['href']}")

        # Get page title and main content structure
        print("\n=== Page Structure ===")
        title = await page.title()
        print(f"Title: {title}")

        # Try to find article list items
        print("\n=== Possible Article Containers ===")
        selectors_to_try = [
            "article", ".article", ".post", ".card",
            "[class*='article']", "[class*='post']", "[class*='content-item']",
            "li a", ".list-item"
        ]
        for sel in selectors_to_try:
            count = await page.locator(sel).count()
            if count > 0:
                print(f"  {sel}: {count} elements")

        # Get raw HTML snippet of the main content area
        print("\n=== Main Content HTML (first 3000 chars) ===")
        html = await page.content()
        # Find the article list area
        start = html.find("vip_content")
        if start == -1:
            start = html.find("article")
        print(html[max(0, start-200):start+3000])

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
