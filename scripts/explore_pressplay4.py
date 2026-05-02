#!/usr/bin/env python3
"""Re-explore PressPlay /articles page: find the article list scroll container."""
import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
ARTICLES_URL = f"https://www.pressplay.cc/member/learning/projects/{PROJECT_ID}/articles"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        page = await context.new_page()
        await page.goto(ARTICLES_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        print(f"Title: {await page.title()}")

        # Find scrollable containers (overflow: auto/scroll)
        print("\n=== Scrollable containers ===")
        scrollables = await page.evaluate("""() => {
            const els = document.querySelectorAll('*');
            const result = [];
            for (const el of els) {
                const style = window.getComputedStyle(el);
                const overflow = style.overflow + style.overflowY;
                if (overflow.includes('auto') || overflow.includes('scroll')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.height > 100 && rect.width > 100) {
                        result.push({
                            tag: el.tagName,
                            class: el.className.slice(0, 60),
                            scrollHeight: el.scrollHeight,
                            clientHeight: el.clientHeight,
                            width: Math.round(rect.width),
                            height: Math.round(rect.height)
                        });
                    }
                }
            }
            return result;
        }""")
        for s in scrollables[:15]:
            print(f"  <{s['tag']}> class={s['class']!r} scrollH={s['scrollHeight']} clientH={s['clientHeight']}")

        # Try scrolling each container and count article links
        print("\n=== Try scrolling containers for article links ===")
        containers = await page.evaluate("""() => {
            const els = document.querySelectorAll('*');
            const result = [];
            for (const el of els) {
                const style = window.getComputedStyle(el);
                const overflow = style.overflow + style.overflowY;
                if ((overflow.includes('auto') || overflow.includes('scroll')) && el.scrollHeight > el.clientHeight + 50) {
                    result.push(el.tagName + '.' + el.className.split(' ')[0]);
                }
            }
            return result;
        }""")
        print(f"Scrollable (with overflow content): {containers}")

        # Scroll each candidate container
        PROJECT_ART_PATTERN = f"/project/{PROJECT_ID}/articles/"
        for selector in containers[:5]:
            tag = selector.split('.')[0]
            cls = selector.split('.')[1] if '.' in selector else ''
            full_sel = f"{tag}.{cls}" if cls else tag
            try:
                count_before = await page.eval_on_selector_all(
                    f"a[href*='{PROJECT_ART_PATTERN}']",
                    "els => els.length"
                )
                # Scroll the container
                for _ in range(10):
                    await page.evaluate(f"""() => {{
                        const el = document.querySelector('{full_sel}');
                        if (el) el.scrollTop += 500;
                    }}""")
                    await page.wait_for_timeout(800)
                count_after = len(await page.eval_on_selector_all(
                    f"a[href*='{PROJECT_ART_PATTERN}']",
                    "els => [...new Set(els.map(e=>e.href))]"
                ))
                if count_after > count_before:
                    print(f"  ✅ '{full_sel}' works! links: {count_before} → {count_after}")
                else:
                    print(f"  '{full_sel}': no change ({count_after} links)")
            except Exception as e:
                print(f"  '{full_sel}': {e}")

        # Current state of article links on page
        print("\n=== Current article links ===")
        art_links = await page.eval_on_selector_all(
            f"a[href*='/project/{PROJECT_ID}/articles/']",
            """els => {
                const seen = new Set();
                return els.map(el => {
                    if (seen.has(el.href)) return null;
                    seen.add(el.href);
                    return {href: el.href, text: el.innerText.trim().slice(0, 70)};
                }).filter(Boolean);
            }"""
        )
        print(f"Total unique article links: {len(art_links)}")
        for l in art_links[:20]:
            print(f"  {l['text']!r} -> .../{l['href'].split('/')[-1]}")

        # Check page HTML for list container
        print("\n=== HTML of article list area ===")
        html = await page.content()
        idx = html.find(PROJECT_ART_PATTERN)
        if idx > 0:
            print(html[max(0,idx-300):idx+200])

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
