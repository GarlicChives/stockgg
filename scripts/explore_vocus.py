#!/usr/bin/env python3
"""Explore Vocus 韭菜王 page structure via CDP."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
BASE_URL = "https://vocus.cc/salon/ChivesKing"


async def explore_page(context, url, label):
    print(f"\n{'='*60}")
    print(f"=== {label} ===")
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    print(f"URL: {page.url}")
    print(f"Title: {await page.title()}")

    # Scroll to trigger lazy load
    for _ in range(3):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)

    # Find article links
    links = await page.eval_on_selector_all(
        "a[href*='/article/'], a[href*='/post/'], a[href*='/p/']",
        "els => [...new Set(els.map(el => ({href:el.href, text:el.innerText.trim().slice(0,70)})))].filter(e=>e.text)"
    )
    print(f"Article links: {len(links)}")
    for l in links[:12]:
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
                    texts.append(f"{t.strip()[:20]!r}|{dt!r}")
            if texts:
                print(f"  '{sel}' ({count}): {'; '.join(texts)}")

    # Pagination
    print("\nPagination:")
    for sel in ["[class*='page']", "[class*='pagination']", "button[class*='more']", "a[rel='next']"]:
        count = await page.locator(sel).count()
        if count > 0:
            t = await page.locator(sel).first.inner_text()
            print(f"  '{sel}' ({count}): {t.strip()[:60]!r}")

    await page.close()


async def explore_article(context, url):
    print(f"\n{'='*60}")
    print(f"=== Article: {url} ===")
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    print(f"Title: {await page.title()}")

    for sel in ["article", ".article", "[class*='article']", "[class*='content']",
                "main", ".post-content", "[class*='post']"]:
        count = await page.locator(sel).count()
        if count > 0:
            text = await page.locator(sel).first.inner_text()
            if len(text) > 300:
                print(f"  '{sel}' (len={len(text)}): {text[50:450]!r}")
                break

    for sel in ["time[datetime]", "time", "[class*='date']", "[class*='publish']",
                "meta[property='article:published_time']"]:
        count = await page.locator(sel).count()
        if count > 0:
            t = await page.locator(sel).first.inner_text()
            dt = await page.locator(sel).first.get_attribute("datetime") or \
                 await page.locator(sel).first.get_attribute("content")
            if t.strip() or dt:
                print(f"  Date '{sel}': {t.strip()[:30]!r} | {dt!r}")
                break

    await page.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        await explore_page(context, BASE_URL, "韭菜王 Salon Index")

        # Try member-only area
        await explore_page(context, "https://vocus.cc/salon/ChivesKing/articles", "韭菜王 Articles")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
