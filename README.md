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
- **熱門題材** — TW main + sub industry ranking on top-N volume stocks (N=`RANKINGS_TOP_N` in `src/utils/config.py`)
- **跨來源議題** — common topics across multiple sources (≥ 2)

## How it's served

```
Supabase DB ──[anon key, 10-pattern allowlist]──▶ Cloudflare Workers
                                                  (public site)
```

1. A separate private system runs daily/hourly: crawl public sources,
   run Whisper transcription, run Gemini analysis, write results to
   Supabase.
2. This repo holds only the **rendering** layer — `generate_html.py`
   reads pre-computed columns from Supabase via a restricted Edge
   Function (`db-proxy-public`).
3. GitHub Actions rebuilds + redeploys when the private system
   webhooks the workflow, plus on cron 07:30 / 18:15 / 23:15 TW.

The Edge Function enforces a hard allowlist of 10 SELECT shapes — even
if the anon key in this repo leaks, raw article bodies and podcast
transcripts are not reachable. The allowlist source is
`supabase/functions/db-proxy-public/index.ts`.

## Code

- `scripts/generate_html.py` — single-file HTML renderer
- `src/analysis/focus_themes.py` — theme dictionary clustering
- `src/utils/db.py` — async DB client over the restricted Edge Function
- `data/theme_dictionary.json` — main/sub industry hierarchy (ticker-centric, TW only)
- `supabase/functions/db-proxy-public/` — Edge Function source

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
