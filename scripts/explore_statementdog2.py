#!/usr/bin/env python3
"""Explore StatementDog article content and all pages."""
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

        # --- Check page 2 ---
        print("=== Page 2 ===")
        page = await context.new_page()
        await page.goto("https://statementdog.com/industry_reports?page=2", wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1500)

        links = await page.eval_on_selector_all(
            "a[href*='/industry_reports/']",
            "els => [...new Set(els.map(el => ({href:el.href, text:el.innerText.trim().slice(0,80)})))].filter(e=>e.text && e.href.match(/\/\d+$/))"
        )
        print(f"Page 2 reports: {len(links)}")
        for l in links[:8]:
            print(f"  {l['text'][:60]!r} -> {l['href']}")

        # Check if page 3 exists
        page3 = await page.locator("a[href*='page=3']").count()
        print(f"Page 3 link exists: {page3 > 0}")
        await page.close()

        # --- Article content ---
        print("\n=== Article Content ===")
        art_url = "https://statementdog.com/industry_reports/42"
        page = await context.new_page()
        await page.goto(art_url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)
        print(f"Title: {await page.title()}")

        # Try content selectors
        for sel in ["article", "main", "[class*='content']", "[class*='article']",
                    "[class*='report']", "[class*='body']", ".prose", "[class*='prose']"]:
            count = await page.locator(sel).count()
            if count > 0:
                text = await page.locator(sel).first.inner_text()
                if len(text) > 400:
                    print(f"\n  '{sel}' len={len(text)}:")
                    print(f"  {text[100:600]!r}")
                    break

        # Date on article page
        for sel in ["time[datetime]", "[class*='date']", "[class*='publish']", "time"]:
            count = await page.locator(sel).count()
            if count > 0:
                t = await page.locator(sel).first.inner_text()
                dt = await page.locator(sel).first.get_attribute("datetime")
                if t.strip() or dt:
                    print(f"\n  Date '{sel}': {t.strip()[:30]!r} | {dt!r}")
                    break

        # Paywall check
        body_text = await page.locator("body").inner_text()
        print(f"\n  Body text length: {len(body_text)}")
        for kw in ["訂閱", "解鎖", "付費", "登入後", "upgrade"]:
            if kw in body_text:
                print(f"  ⚠️  Found '{kw}' — possible paywall")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
