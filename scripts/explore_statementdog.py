#!/usr/bin/env python3
"""Explore StatementDog industry reports page structure via CDP."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
BASE_URL = "https://statementdog.com/industry_reports"


async def explore_page(context, url, label):
    print(f"\n{'='*60}")
    print(f"=== {label} ===")
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle", timeout=45000)
    await page.wait_for_timeout(2000)
    print(f"URL: {page.url}")
    print(f"Title: {await page.title()}")

    # Scroll a few times
    for _ in range(3):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)

    # Article links
    links = await page.eval_on_selector_all(
        "a[href*='/industry_report'], a[href*='/report'], a[href*='/analysis'], a[href*='/post'], a[href*='/article']",
        "els => [...new Set(els.map(el => ({href:el.href, text:el.innerText.trim().slice(0,70)})))].filter(e=>e.text)"
    )
    print(f"Article-like links: {len(links)}")
    for l in links[:12]:
        print(f"  {l['text']!r} -> {l['href']}")

    # All hrefs on page (statementdog domain)
    all_hrefs = await page.eval_on_selector_all(
        "a[href]",
        "els => [...new Set(els.map(el=>el.href).filter(h=>h.includes('statementdog.com')))]"
    )
    print(f"\nAll statementdog.com hrefs ({len(all_hrefs)}):")
    for h in sorted(set(all_hrefs))[:30]:
        print(f"  {h}")

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

    await page.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]
        await explore_page(context, BASE_URL, "財報狗 Industry Reports")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
