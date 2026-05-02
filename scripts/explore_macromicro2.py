#!/usr/bin/env python3
"""Explore MacroMicro blog and monthly report structure."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))


async def explore_page(context, url, label):
    print(f"\n{'='*60}")
    print(f"=== {label}: {url} ===")
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    # Scroll down to load more content
    for _ in range(3):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)

    print(f"Title: {await page.title()}")

    # Find article links
    blog_links = await page.eval_on_selector_all(
        "a[href*='/blog/'], a[href*='/mails/']",
        "els => [...new Set(els.map(el => ({href:el.href, text:el.innerText.trim().slice(0,70)})))].filter(e=>e.text && !e.href.endsWith('/blog') && !e.href.endsWith('/blog/'))"
    )
    print(f"Article links found: {len(blog_links)}")
    for l in blog_links[:10]:
        print(f"  {l['text']!r}")
        print(f"    -> {l['href']}")

    # Check for date elements
    print("\nDate elements:")
    for sel in ["time", "[class*='date']", "[class*='time']", "[class*='publish']"]:
        count = await page.locator(sel).count()
        if count > 0:
            texts = []
            for i in range(min(3, count)):
                t = await page.locator(sel).nth(i).inner_text()
                dt = await page.locator(sel).nth(i).get_attribute("datetime")
                texts.append(f"text={t.strip()[:30]!r} datetime={dt!r}")
            print(f"  '{sel}' ({count}): {'; '.join(texts)}")

    # Check for load more / pagination
    print("\nPagination/Load more:")
    for sel in ["[class*='more']", "[class*='page']", "button", "[class*='load']"]:
        count = await page.locator(sel).count()
        if count > 0:
            for i in range(min(5, count)):
                t = await page.locator(sel).nth(i).inner_text()
                if t.strip():
                    print(f"  '{sel}': {t.strip()[:40]!r}")

    await page.close()


async def explore_article(context, url):
    print(f"\n{'='*60}")
    print(f"=== Single Article: {url} ===")
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    print(f"Title: {await page.title()}")

    # Try content selectors
    for sel in ["article", ".article", "[class*='article']", "[class*='content']",
                "main", ".post", "[class*='post']", "[class*='body']"]:
        count = await page.locator(sel).count()
        if count > 0:
            text = await page.locator(sel).first.inner_text()
            if len(text) > 300:
                print(f"\n  '{sel}' (length={len(text)}):")
                print(f"  {text[100:500]!r}")
                break

    # Date
    for sel in ["time[datetime]", "time", "[class*='date']", "[class*='publish']",
                "meta[property='article:published_time']"]:
        count = await page.locator(sel).count()
        if count > 0:
            t = await page.locator(sel).first.inner_text()
            dt = await page.locator(sel).first.get_attribute("datetime") or \
                 await page.locator(sel).first.get_attribute("content")
            if t.strip() or dt:
                print(f"\n  Date '{sel}': text={t.strip()[:30]!r}, attr={dt!r}")
                break

    await page.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        await explore_page(context, "https://www.macromicro.me/blog", "Blog Index")
        await explore_page(context, "https://www.macromicro.me/mails/monthly_report", "Monthly Reports")

        # Explore one blog article
        await explore_article(
            context,
            "https://www.macromicro.me/blog/founder-s-article-middle-east-risks-are-fluctuating-and-the-market-is-strongly-returning-to-fundamental-pricing"
        )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
