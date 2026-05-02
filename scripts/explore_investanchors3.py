#!/usr/bin/env python3
"""Explore InvestAnchors: pagination URLs and article content selectors."""
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

        page = await context.new_page()
        await page.goto(INDEX_URL, wait_until="networkidle")
        await page.wait_for_timeout(1000)

        # Find pagination buttons and their hrefs
        print("=== Pagination Links ===")
        page_links = await page.eval_on_selector_all(
            "a[href*='page'], a[href*='Page'], [class*='page'] a, [class*='pagination'] a",
            "els => els.map(el => ({href: el.href, text: el.innerText.trim()}))"
        )
        print(f"Found {len(page_links)} pagination links:")
        for l in page_links:
            print(f"  {l['text']!r} -> {l['href']}")

        # Try clicking page 2
        print("\n=== Trying to click page 2 ===")
        page2_btn = page.locator("text='2'").first
        if await page2_btn.count() > 0:
            current_url = page.url
            await page2_btn.click()
            await page.wait_for_timeout(2000)
            new_url = page.url
            print(f"URL changed: {current_url} -> {new_url}")

            links = await page.eval_on_selector_all(
                "a[href*='/vip_contents/']",
                "els => els.map(el => el.href).filter(h => !h.includes('index'))"
            )
            print(f"Page 2 articles: {len(links)}")
            for l in links[:5]:
                print(f"  {l}")

        # Explore article content selectors more carefully
        print("\n=== Article Content Deep Dive ===")
        article_url = "https://investanchors.com/user/vip_contents/17772959451558"
        art_page = await context.new_page()
        await art_page.goto(article_url, wait_until="networkidle")
        await art_page.wait_for_timeout(2000)

        # Find the specific article body
        selectors = [
            ".vip-content", ".post-content", ".article-content",
            "#content", "#article", ".email-content",
            "[class*='vip']", "[class*='letter']", "[class*='body']",
            "section", "main article", ".container .content"
        ]
        for sel in selectors:
            count = await art_page.locator(sel).count()
            if count > 0:
                text = await art_page.locator(sel).first.inner_text()
                if len(text) > 200:
                    print(f"\n  '{sel}' ({count} matches), length={len(text)}")
                    print(f"  Preview: {text[200:600]!r}")

        # Get published_at from the page HTML
        print("\n=== Date extraction ===")
        html = await art_page.content()
        # Find date patterns
        import re
        dates = re.findall(r'(\d{4}[年-]\d{1,2}[月-]\d{1,2})', html)
        print(f"Date patterns found: {list(set(dates))[:10]}")

        # Check for structured date data
        for sel in ["time[datetime]", "meta[property='article:published_time']",
                    "[class*='created']", "[class*='publish']"]:
            count = await art_page.locator(sel).count()
            if count > 0:
                text = await art_page.locator(sel).first.inner_text()
                dt = await art_page.locator(sel).first.get_attribute("datetime") or \
                     await art_page.locator(sel).first.get_attribute("content")
                print(f"  '{sel}': text={text.strip()[:50]!r}, attr={dt!r}")

        await art_page.close()
        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
