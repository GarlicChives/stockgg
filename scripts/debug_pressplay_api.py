#!/usr/bin/env python3
"""Debug PressPlay timelines/v2 POST body and replay."""
import asyncio
import json
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

        captured_req = []
        captured_resp = []

        async def handle_request(req):
            if 'og-web.pressplay.cc' in req.url and 'timelines' in req.url:
                captured_req.append({
                    'url': req.url,
                    'method': req.method,
                    'post_data': req.post_data,
                    'headers': dict(req.headers),
                })

        async def handle_response(resp):
            if 'og-web.pressplay.cc' in resp.url and 'timelines' in resp.url:
                try:
                    body = await resp.text()
                    captured_resp.append({'url': resp.url, 'status': resp.status, 'body': body})
                except Exception:
                    pass

        page.on("request", handle_request)
        page.on("response", handle_response)
        await page.goto(ARTICLES_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(2000)

        print("=== CAPTURED REQUESTS ===")
        for r in captured_req:
            print(f"Method: {r['method']}")
            print(f"URL: {r['url']}")
            print(f"POST body: {r['post_data']}")
            print("Headers:")
            for k, v in r['headers'].items():
                print(f"  {k}: {v}")
            print()

        print("=== CAPTURED RESPONSES ===")
        for r in captured_resp:
            print(f"Status: {r['status']} — {r['url'][:80]}")
            data = json.loads(r['body'])
            lst = data.get('data', {}).get('list', [])
            meta = data.get('data', {}).get('meta', {})
            print(f"  Articles in list: {len(lst)}")
            print(f"  Meta: {meta}")
            if lst:
                print(f"  First article keys: {list(lst[0].keys())}")
                print(f"  First: [{lst[0].get('release_time','')[:10]}] {lst[0].get('timeline_title','')[:60]!r}")
                print(f"  timeline_key: {lst[0].get('timeline_key','')[:40]}")
            print()

        # Now replay with page modification
        if captured_req:
            req = captured_req[0]
            body_obj = json.loads(req['post_data'])
            print(f"=== Full POST body ===")
            print(json.dumps(body_obj, ensure_ascii=False, indent=2))

            # Test page 1 and page 2
            for pg in [1, 2]:
                body_obj['page'] = pg
                body_str = json.dumps(body_obj)
                safe_headers = {k: v for k, v in req['headers'].items()
                               if not k.startswith(':')}
                result = await page.evaluate("""async ([url, body, hdrs]) => {
                    const r = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        headers: Object.assign({'content-type': 'application/json'}, hdrs),
                        body: body
                    });
                    return {status: r.status, body: await r.text()};
                }""", [req['url'], body_str, safe_headers])
                print(f"\nReplay page={pg}: status={result['status']}")
                try:
                    data = json.loads(result['body'])
                    lst = data.get('data', {}).get('list', [])
                    meta = data.get('data', {}).get('meta', {})
                    print(f"  Articles: {len(lst)}, Meta: {meta}")
                    for a in lst[:3]:
                        print(f"  [{a.get('release_time','')[:10]}] {a.get('timeline_title','')[:60]!r}")
                except Exception:
                    print(f"  Body: {result['body'][:300]}")

        await page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
