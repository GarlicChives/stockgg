#!/usr/bin/env python3
"""Explore MacroMicro page structure via CDP."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
BASE_URL = "https://www.macromicro.me"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        # Find existing macromicro tab
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "macromicro" in pg.url:
                    page = pg
                    break

        if page is None:
            page = await context.new_page()
            await page.goto(BASE_URL, wait_until="networkidle")
        else:
            print(f"Using existing tab: {page.url}")
            await page.bring_to_front()

        await page.wait_for_timeout(2000)

        print(f"Current URL: {page.url}")
        print(f"Title: {await page.title()}")

        # Look for article/research links
        print("\n=== Navigation Links ===")
        nav_links = await page.eval_on_selector_all(
            "nav a, header a, [class*='nav'] a, [class*='menu'] a",
            "els => els.map(el => ({href: el.href, text: el.innerText.trim()})).filter(e => e.text)"
        )
        for l in nav_links[:20]:
            print(f"  {l['text']!r} -> {l['href']}")

        # Look for research/report section
        print("\n=== Research/Article Links ===")
        article_links = await page.eval_on_selector_all(
            "a[href*='/research'], a[href*='/report'], a[href*='/post'], a[href*='/article'], a[href*='/stories']",
            "els => els.map(el => ({href: el.href, text: el.innerText.trim().slice(0,60)})).filter(e=>e.text)"
        )
        for l in article_links[:15]:
            print(f"  {l['text']!r} -> {l['href']}")

        # Check subscription/member area
        print("\n=== Member/VIP Links ===")
        member_links = await page.eval_on_selector_all(
            "a[href*='/member'], a[href*='/vip'], a[href*='/premium'], a[href*='/subscribe']",
            "els => els.map(el => ({href: el.href, text: el.innerText.trim().slice(0,60)})).filter(e=>e.text)"
        )
        for l in member_links[:10]:
            print(f"  {l['text']!r} -> {l['href']}")

        # Get all links on page
        print("\n=== All unique hrefs (filtered) ===")
        all_links = await page.eval_on_selector_all(
            "a[href]",
            "els => [...new Set(els.map(el => el.href))].filter(h => h.includes('macromicro'))"
        )
        for l in sorted(set(all_links))[:30]:
            print(f"  {l}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
