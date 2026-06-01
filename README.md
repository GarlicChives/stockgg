# StockGG

Public-facing daily-briefing site for Taiwan + US equity markets.

**Live**: <https://stockgg.v4578469.workers.dev>

The site renders pre-computed AI analysis + public market data into a
single-page HTML and serves it through Cloudflare Workers. It does not
host, mirror, or display subscription-content article bodies or podcast
transcripts — all of that lives in a separate private system.

## What you see on the page

- **今日焦點** — analyst-style morning note (4-7 themed bullet points,
  Gemini-generated)
- **今日國際指標** — US / TW / JP / KR index movements + VIX / 10Y / DXY /
  Fear & Greed
- **綜合多空判斷** — short / medium / long term direction tags
- **未來事件日曆** — upcoming earnings (yfinance) + macro events
- **熱門題材** — TW sub-industry ranking on top-N volume stocks (N=`RANKINGS_TOP_N` in `src/utils/config.py`); subs with identical focal sets get merged ("A & B & C: stocks"). 加 cluster sort chip(TV / 漲跌 / 殖利率)、universal ticker filter、per-cluster 6m 資金淨流入 sparkline → 點開 modal 大圖 + 1M/3M/6M/1Y/ALL 時間粒度切換
- **跨來源議題** — common topics across multiple sources (≥ 2)
- **焦點排行** — Top 15 高殖利率 + Top 15 低 PE,各帶 CSV 下載
- **股票 modal** — 點任一 ticker pill 開啟,顯示該檔被哪些主動式 ETF 持有(持股市值 / 佔流通比 / 加減碼)
- **站內搜尋** — header 右側輸入 ticker / 公司名 / 子產業關鍵字,鍵盤導覽 + 跳到對應 cluster 卡片 + highlight 動畫
- **分享 + SEO** — Open Graph + Twitter Card,footer 4 顆 share button(Line/X/Facebook/複製連結),mobile 出現原生 share

## How it's served

```
Supabase DB ──[anon key, 23-pattern allowlist]──▶ Cloudflare Workers
                                                  (public site)
                                                  ├ docs/index.html (inline state + per-render payload)
                                                  ├ docs/style.css / docs/app.js (static, content-hash cache-bust)
                                                  ├ docs/history.json (chart modal, lazy fetch)
                                                  └ docs/kline.json (個股 K 線, ~8MB, lazy fetch)
```

1. A separate private system runs daily/hourly: crawl public sources,
   run Whisper transcription, run Gemini analysis, write results to
   Supabase.
2. This repo holds only the **rendering** layer — `generate_html.py`
   reads pre-computed columns from Supabase via a restricted Edge
   Function (`db-proxy-public`).
3. GitHub Actions rebuilds + redeploys when the private system
   webhooks the workflow, plus on cron 07:30 / 18:15 / 23:15 TW.

The Edge Function enforces a hard allowlist of 23 SELECT shapes — even
if the anon key in this repo leaks, raw article bodies and podcast
transcripts are not reachable. The allowlist source is
`supabase/functions/db-proxy-public/index.ts`.

## Code

- `scripts/generate_html.py` — HTML renderer (~2700 lines, page structure + data payload; CSS/JS extracted to standalone files since 2026-05)
- `docs/style.css`, `docs/app.js` — static CSS/JS, edited directly; generate_html.py content-hash cache-busts the `?v=` query
- `src/analysis/focus_themes.py` — theme dictionary clustering
- `src/utils/db.py` — async DB client over the restricted Edge Function
- `data/theme_dictionary.json` — main/sub industry hierarchy (ticker-centric, TW only)
- `supabase/functions/db-proxy-public/` — Edge Function source (23-pattern allowlist)
- `docs/index.html`, `docs/history.json`, `docs/kline.json` — generated artifacts served by Workers
- `wrangler.jsonc` — Workers config (assets.directory: docs)

## Local development

```bash
uv sync
cp .env.example .env  # then fill SUPABASE_ANON_KEY
uv run python scripts/generate_html.py
open docs/index.html
```

## Disclaimer

See LICENSE for software terms and `<footer>` in the rendered page for
the investment-content disclaimer. The site is for personal reference
only and does not constitute investment advice.
