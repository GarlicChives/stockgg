#!/usr/bin/env python3
"""Intercept PressPlay network requests to find article list API."""
import asyncio
import json
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
CDP_PORT = int(os.environ.get("CHROME_DEBUG_PORT", 9222))
PROJECT_ID = "EFB905DAF7B44F479552E5F5D955A137"
ARTICLES_URL = f"https://www.pressplay.cc/member/learning/projects/{PROJECT_ID}/articles"
ART_URL = f"https://www.pressplay.cc/project/{PROJECT_ID}/articles/3C9E58847448C89CB0C68C4856808EE3"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = browser.contexts[0]

        captured = []

        page = await context.new_page()

        # Capture all API/XHR requests
        async def on_request(req):
            url = req.url
            if any(x in url for x in ['api', 'graphql', 'article', 'project', 'lesson', 'content']):
                captured.append({'type': 'req', 'method': req.method, 'url': url})

        async def on_response(resp):
            url = resp.url
            if any(x in url for x in ['api', 'graphql', 'article', 'project', 'lesson', 'content']):
                try:
                    body = await resp.text()
                    if len(body) > 50 and (body.startswith('{') or body.startswith('[')):
                        captured.append({'type': 'resp', 'url': url, 'body': body[:500]})
                except:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        # Load articles index
        print("=== Loading /articles index page ===")
        await page.goto(ARTICLES_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        print(f"Captured {len(captured)} API calls:")
        for c in captured:
            if c['type'] == 'resp':
                print(f"  RESP {c['url'][:100]}")
                print(f"       {c['body'][:200]}")
            else:
                print(f"  {c['method']} {c['url'][:100]}")

        captured.clear()

        # Load article page and scroll sidebar
        print("\n=== Loading article page ===")
        await page.goto(ART_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        # Scroll the sidebar
        await page.evaluate("""() => {
            const el = document.querySelector('.os-viewport');
            if (el) {
                for (let i = 0; i < 20; i++) el.scrollTop += 200;
            }
        }""")
        await page.wait_for_timeout(3000)

        print(f"Captured {len(captured)} API calls after article load + scroll:")
        for c in captured:
            if c['type'] == 'resp':
                print(f"  RESP {c['url'][:120]}")
                print(f"       {c['body'][:300]}")
            else:
                print(f"  {c['method']} {c['url'][:120]}")

        # Check JS state for article list
        print("\n=== JS state / window variables ===")
        js_state = await page.evaluate("""() => {
            const keys = Object.keys(window).filter(k =>
                k.includes('article') || k.includes('lesson') || k.includes('project') ||
                k.includes('__') || k.includes('data') || k.includes('store')
            );
            const result = {};
            for (const k of keys.slice(0, 20)) {
                try {
                    const v = window[k];
                    if (typeof v === 'object' && v !== null) {
                        result[k] = JSON.stringify(v).slice(0, 200);
                    }
                } catch(e) {}
            }
            return result;
        }""")
        for k, v in js_state.items():
            print(f"  window.{k}: {v[:150]}")

        # Get all links inside sidebar container
        print("\n=== Sidebar article links ===")
        sidebar_links = await page.eval_on_selector_all(
            f".os-viewport a[href*='/project/{PROJECT_ID}/articles/']",
            """els => els.map(el => {
                const p = el.closest('li') || el.parentElement;
                return {
                    href: el.href,
                    text: el.innerText.trim().slice(0, 70),
                    parent: p ? p.innerText.trim().slice(0, 80) : ''
                };
            })"""
        )
        print(f"Sidebar links: {len(sidebar_links)}")
        for l in sidebar_links[:15]:
            print(f"  {l['text']!r} | parent={l['parent'][:40]!r}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
