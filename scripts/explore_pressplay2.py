#!/usr/bin/env python3
"""Deep dive into PressPlay article link structure."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
BASE_URL = "https://www.pressplay.cc/member/learning/projects/EFB905DAF7B44F479552E5F5D955A137/articles"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        # Scroll more to load articles
        print("Scrolling to load articles...")
        for i in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            body_len = len(await page.locator("body").inner_text())
            print(f"  Scroll {i+1}: body text len={body_len}")

        # Get ALL links on page
        print("\n=== All clickable links ===")
        all_links = await page.eval_on_selector_all(
            "a[href]",
            """els => els.map(el => ({
                href: el.href,
                text: el.innerText.trim().slice(0, 60)
            })).filter(e => e.text)"""
        )
        seen = set()
        for l in all_links:
            if l['href'] not in seen:
                seen.add(l['href'])
                print(f"  {l['text']!r} -> {l['href']}")

        # Get page HTML to find article containers
        print("\n=== Article cards HTML structure ===")
        html = await page.content()
        # Look for article title patterns
        import re
        titles = re.findall(r'產業趨勢.{0,5}#\d+|【.*?】.*?(?=<)', html)
        print(f"Found title patterns: {titles[:5]}")

        # Try to find clickable article items (not just <a> tags)
        print("\n=== Clickable elements with article-like text ===")
        clickables = await page.eval_on_selector_all(
            "[onclick], [role='link'], [class*='card'], [class*='item'], [class*='article'], li",
            """els => els.map(el => {
                const text = el.innerText?.trim().slice(0, 80) || '';
                const href = el.getAttribute('href') || el.getAttribute('onclick') || '';
                return text.length > 20 ? {text, href, tag: el.tagName} : null;
            }).filter(Boolean)"""
        )
        for c in clickables[:20]:
            print(f"  <{c['tag']}> {c['text']!r} | {c['href'][:60]!r}")

        # Check directory page for article list
        print("\n=== Directory page ===")
        dir_page = await context.new_page()
        await dir_page.goto(
            "https://www.pressplay.cc/member/learning/projects/EFB905DAF7B44F479552E5F5D955A137/directory",
            wait_until="networkidle", timeout=45000
        )
        await dir_page.wait_for_timeout(2000)
        dir_links = await dir_page.eval_on_selector_all(
            "a[href]",
            "els => [...new Set(els.map(el=>({href:el.href,text:el.innerText.trim().slice(0,70)})))].filter(e=>e.text&&e.href.includes('pressplay'))"
        )
        print(f"Directory links ({len(dir_links)}):")
        for l in dir_links[:20]:
            print(f"  {l['text']!r} -> {l['href']}")

        body_dir = await dir_page.locator("body").inner_text()
        print(f"\nDirectory body (first 800 chars):\n{body_dir[:800]!r}")
        await dir_page.close()

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
