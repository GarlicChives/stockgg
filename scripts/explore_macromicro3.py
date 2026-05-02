#!/usr/bin/env python3
"""Explore MacroMicro report content and blog infinite scroll."""
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

        # --- 1. Blog infinite scroll depth ---
        print("=== Blog: Infinite scroll test ===")
        page = await context.new_page()
        await page.goto("https://www.macromicro.me/blog", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        prev_count = 0
        for i in range(15):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            links = await page.eval_on_selector_all(
                "time[datetime]",
                "els => els.map(el => el.getAttribute('datetime'))"
            )
            unique_dates = list(dict.fromkeys(links))
            print(f"  Scroll {i+1}: {len(unique_dates)} dated articles, oldest={unique_dates[-1] if unique_dates else 'N/A'}")
            if len(unique_dates) == prev_count and i > 2:
                print("  No more loading.")
                break
            prev_count = len(unique_dates)
            # Stop if oldest article is beyond 6 months
            if unique_dates and unique_dates[-1] < "2025-11-01":
                print(f"  Reached 6-month cutoff at {unique_dates[-1]}")
                break

        # Collect all blog articles with dates
        all_blog = await page.eval_on_selector_all(
            "a[href*='/blog/']:has(time), a[href*='/blog/']",
            r"""els => {
                const seen = new Set();
                return els.map(el => {
                    const href = el.href;
                    if (seen.has(href) || href.includes('/tag/') || href === 'https://www.macromicro.me/blog' || href.endsWith('/blog/')) return null;
                    seen.add(href);
                    const timeEl = el.querySelector('time') || el.closest('[class]')?.querySelector('time');
                    const dt = timeEl ? timeEl.getAttribute('datetime') : '';
                    const titleEl = el.querySelector('h2,h3,h4,[class*="title"]') || el;
                    return {href, title: titleEl.innerText.trim().slice(0, 80), date: dt};
                }).filter(Boolean);
            }"""
        )
        print(f"\nTotal unique blog articles: {len(all_blog)}")
        for a in all_blog[:5]:
            print(f"  [{a['date']}] {a['title']!r}")
        await page.close()

        # --- 2. Monthly report content structure ---
        print("\n=== Monthly Report content ===")
        report_url = "https://www.macromicro.me/mails/monthly_report_v2/display/102"
        page = await context.new_page()
        await page.goto(report_url, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        print(f"Title: {await page.title()}")
        for sel in ["article", "main", ".content", "[class*='mail']", "[class*='report']", "body"]:
            count = await page.locator(sel).count()
            if count > 0:
                text = await page.locator(sel).first.inner_text()
                if len(text) > 200:
                    print(f"  '{sel}' length={len(text)}: {text[:400]!r}")
                    break
        await page.close()

        # --- 3. EDM (快報) content structure ---
        print("\n=== EDM Quick Report content ===")
        edm_url = "https://www.macromicro.me/mails/edm/tc/display/2007/86836602/b05c1e9"
        page = await context.new_page()
        await page.goto(edm_url, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        print(f"Title: {await page.title()}")
        for sel in ["article", "main", ".content", "[class*='mail']", "body"]:
            count = await page.locator(sel).count()
            if count > 0:
                text = await page.locator(sel).first.inner_text()
                if len(text) > 200:
                    print(f"  '{sel}' length={len(text)}: {text[:400]!r}")
                    break
        await page.close()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
