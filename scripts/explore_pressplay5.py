#!/usr/bin/env python3
"""Explore PressPlay article view sidebar — find the full article list container."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
# Use a known recent article
ART_URL = f"https://www.pressplay.cc/project/{PROJECT_ID}/articles/3C9E58847448C89CB0C68C4856808EE3"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        page = await context.new_page()
        await page.goto(ART_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)
        print(f"Title: {await page.title()}")

        # Find ALL scrollable containers with meaningful scroll depth
        print("\n=== All scrollable containers ===")
        scrollables = await page.evaluate("""() => {
            const result = [];
            for (const el of document.querySelectorAll('*')) {
                const style = window.getComputedStyle(el);
                const oy = style.overflowY;
                if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {
                    const rect = el.getBoundingClientRect();
                    result.push({
                        tag: el.tagName,
                        cls: el.className.slice(0, 80),
                        id: el.id,
                        scrollH: el.scrollHeight,
                        clientH: el.clientHeight,
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        x: Math.round(rect.x)
                    });
                }
            }
            return result;
        }""")
        for s in scrollables:
            print(f"  <{s['tag']}> id={s['id']!r} cls={s['cls'][:50]!r} scrollH={s['scrollH']} clientH={s['clientH']} w={s['w']} x={s['x']}")

        # Try scrolling each container and count article links loaded
        print("\n=== Scroll each container to find article list ===")
        PROJECT_PAT = f"/project/{PROJECT_ID}/articles/"

        for i, s in enumerate(scrollables):
            cls_parts = s['cls'].strip().split()
            sel = f"#{s['id']}" if s['id'] else (f".{cls_parts[0]}" if cls_parts else s['tag'])

            links_before = await page.eval_on_selector_all(
                f"a[href*='{PROJECT_PAT}']", "els => [...new Set(els.map(e=>e.href))].length"
            )

            # Scroll the container 15 times
            for _ in range(15):
                await page.evaluate(f"""() => {{
                    const el = document.querySelector({sel!r});
                    if (el) el.scrollTop += 300;
                }}""")
                await page.wait_for_timeout(500)

            links_after = len(await page.eval_on_selector_all(
                f"a[href*='{PROJECT_PAT}']", "els => [...new Set(els.map(e=>e.href))]"
            ))

            delta = links_after - links_before
            marker = "✅" if delta > 0 else "  "
            print(f"  {marker} [{i}] {sel!r}: {links_before} → {links_after} (+{delta})")

            if delta > 0:
                # Found the right container, scroll it all the way
                print(f"    ▶ Scrolling {sel!r} to load all articles...")
                prev = links_after
                for _ in range(50):
                    await page.evaluate(f"""() => {{
                        const el = document.querySelector({sel!r});
                        if (el) el.scrollTop += 500;
                    }}""")
                    await page.wait_for_timeout(600)
                    now = len(await page.eval_on_selector_all(
                        f"a[href*='{PROJECT_PAT}']", "els => [...new Set(els.map(e=>e.href))]"
                    ))
                    if now == prev:
                        break
                    prev = now

                # Collect all article links with titles and dates
                all_arts = await page.eval_on_selector_all(
                    f"a[href*='{PROJECT_PAT}']",
                    """els => {
                        const seen = new Set();
                        return els.map(el => {
                            if (seen.has(el.href)) return null;
                            seen.add(el.href);
                            const card = el.closest('li') || el.closest('[class*="item"]') || el.parentElement;
                            const timeEl = card ? card.querySelector('time') : null;
                            return {
                                href: el.href,
                                text: el.innerText.trim().slice(0, 80),
                                time: timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText.trim()) : ''
                            };
                        }).filter(Boolean);
                    }"""
                )
                print(f"    Total articles found: {len(all_arts)}")
                for a in all_arts[:10]:
                    print(f"      [{a['time'][:16]}] {a['text']!r}")
                break

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
