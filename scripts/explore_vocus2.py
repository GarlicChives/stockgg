#!/usr/bin/env python3
"""Explore Vocus 韭菜王: scroll depth and article content."""
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

        # --- 1. Scroll depth on index ---
        print("=== Scroll depth test ===")
        page = await context.new_page()
        await page.goto("https://vocus.cc/salon/ChivesKing", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        prev_count = 0
        oldest_date = ""
        for i in range(20):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            items = await page.eval_on_selector_all(
                "a[href*='/article/']",
                r"""els => {
                    const seen = new Set();
                    return els.map(el => {
                        if (seen.has(el.href)) return null;
                        seen.add(el.href);
                        const card = el.closest('li') || el.closest('[class*="card"]') || el.closest('[class*="item"]') || el.parentElement;
                        const timeEl = card ? card.querySelector('time[datetime]') : null;
                        return {href: el.href, date: timeEl ? timeEl.getAttribute('datetime') : ''};
                    }).filter(Boolean);
                }"""
            )
            unique = [x for x in items if x["href"] and "vocus.cc/article/" in x["href"]]
            dates = sorted([x["date"] for x in unique if x["date"]], reverse=True)
            oldest = dates[-1][:10] if dates else "?"

            print(f"  Scroll {i+1}: {len(unique)} unique articles, oldest={oldest}")

            if len(unique) == prev_count and i > 2:
                print("  No more loading.")
                break
            prev_count = len(unique)
            oldest_date = oldest

            if oldest < "2025-11-01":
                print(f"  Reached 6-month cutoff.")
                break

        print(f"\nFinal: {prev_count} articles, oldest={oldest_date}")

        # Collect all with dates
        all_items = await page.eval_on_selector_all(
            "a[href*='/article/']",
            r"""els => {
                const seen = new Set();
                return els.map(el => {
                    if (seen.has(el.href)) return null;
                    seen.add(el.href);
                    const card = el.closest('li') || el.closest('[class*="card"]') || el.closest('[class*="item"]') || el.parentElement;
                    const timeEl = card ? card.querySelector('time[datetime]') : null;
                    const titleEl = card ? (card.querySelector('h2,h3,[class*="title"]') || el) : el;
                    return {
                        href: el.href,
                        title: titleEl.innerText.trim().split('\n')[0].slice(0,80),
                        date: timeEl ? timeEl.getAttribute('datetime') : ''
                    };
                }).filter(Boolean);
            }"""
        )
        print(f"\nSample articles:")
        for a in all_items[:6]:
            print(f"  [{a['date'][:10]}] {a['title']!r}")

        await page.close()

        # --- 2. Single article content ---
        print("\n=== Article content structure ===")
        art_url = "https://vocus.cc/article/69e15c03fd89780001eb324f"
        page = await context.new_page()
        await page.goto(art_url, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        print(f"Title: {await page.title()}")

        for sel in ["article", ".article", "[class*='article']", "[class*='content']",
                    "main", "[class*='post']", "[class*='editor']", "[class*='body']"]:
            count = await page.locator(sel).count()
            if count > 0:
                text = await page.locator(sel).first.inner_text()
                if len(text) > 500:
                    print(f"  '{sel}' len={len(text)}: {text[50:500]!r}")
                    break

        # Check if content is member-only locked
        locked = await page.locator("text=訂閱, text=解鎖, text=付費").count()
        print(f"  Locked indicators: {locked}")

        # Date
        time_el = page.locator("time[datetime]").first
        if await time_el.count() > 0:
            dt = await time_el.get_attribute("datetime")
            print(f"  Date: {dt}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
