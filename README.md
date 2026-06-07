# StockGG

Public-facing daily-briefing site for Taiwan + US equity markets.

**Live**: <https://stockgg.v4578469.workers.dev>

The site renders pre-computed AI analysis + public market data into a
single-page HTML and serves it through Cloudflare Workers. It does not
host, mirror, or display subscription-content article bodies or podcast
transcripts — all of that lives in a separate private system.

## What you see on the page

單頁 SPA,頂部 6 個主頁籤:

- **熱門題材**(首頁)— 台股子產業題材叢集排行(由 top-N 成交值股票 +「近一年焦點」
  字典推導)。兩 sub-tab:🌟 焦點(`hl_sub`,人工編彙長線題材 + 前哨股)/ 📊 泛分類
  (`pan_sub`,statementdog 分類)。每卡顯平均漲跌 / 乖離 / PE / PEG / 殖利 / β + 6m
  資金淨流入 sparkline;外層 sort chip、「多題材股」chip(點擊篩出該股所屬題材並
  highlight)、點 sparkline 開 modal 大圖(疊大盤 / 櫃買 + 三大法人,1M~ALL 粒度)
- **選股雷達** — 焦點股多條件選股,sub-tab:🎯 交集股(符合 ≥2 條件)/ 📊 出量股 /
  🚀 潛力股(多頭排列 / 糾結突破 / 回踩)/ ⛰ 新高股(150 日)/ 🌱 成長股(營收+獲利
  YoY)/ 🔒 籌碼股(法人 / 大戶集保)/ 🥣 看高做低股(碗型底帶量突破頸線,回測最佳參數)。
  全欄位可排序;交集股可多條件篩選
- **主動式 ETF** — 純台股 AUM 前 10 大主動式 ETF:頂部「每日加減碼趨勢」圖
  (逐日跨 ETF 加 / 減碼金額)+「資料已更新 n/total」(每交易日 13:30 收盤後歸零、
  隨各家公布回補)→ 橫排 sub-tab 切各檔:資訊 bar / 今日異動(加減碼 / 新增 / 清倉)/ 全持股
- **市場話題** — 每日分析報告 + 跨來源共同議題(≥ 2 來源)
- **國際金融** — 每日分析報告、未來事件日曆(catalyst events,前 2 週~後 3 週)、
  美股 / 台股成交值前 N 名
- **📈 趨勢** — 大盤 / 櫃買 K 線 + MA + 風險指標燈號(z-score 綜合,回測校準)、
  新高股數 / 籌碼股數 / 大盤距 MA60 偏離 % 副圖

通用:**個股 modal**(點任一 ticker → 日 K 線 + 被哪些主動式 ETF 持有)、**站內搜尋**
(ticker / 公司名 / 題材,鍵盤導覽 + highlight)、**CSV 下載**、**分享 + SEO**
(Open Graph / Twitter Card + Line/X/FB/複製連結)。

## How it's served

```
Supabase DB ──[anon key, 35-pattern allowlist]──▶ Cloudflare Workers
                                                  (public site)
                                                  ├ docs/index.html (inline state + per-render payload)
                                                  ├ docs/style.css / docs/app.js (static, content-hash cache-bust)
                                                  ├ docs/history.json (chart modal, lazy fetch)
                                                  └ docs/kline.json (個股 K 線, ~9MB, lazy fetch)
```

1. A separate private system runs daily/hourly: crawl public sources,
   run Whisper transcription, run Gemini analysis, write results to
   Supabase.
2. This repo holds only the **rendering** layer — `generate_html.py`
   reads pre-computed columns from Supabase via a restricted Edge
   Function (`db-proxy-public`).
3. GitHub Actions rebuilds + redeploys when the private system
   webhooks the workflow, plus on cron 07:30 / 18:15 / 23:15 TW.

The Edge Function enforces a hard allowlist of 35 SELECT shapes — even
if the anon key in this repo leaks, raw article bodies and podcast
transcripts are not reachable. The allowlist source is
`supabase/functions/db-proxy-public/index.ts`.

## Code

- `scripts/generate_html.py` — HTML renderer (~4200 lines, page structure + data payload; CSS/JS extracted to standalone files since 2026-05)
- `docs/style.css`, `docs/app.js` — static CSS/JS, edited directly; generate_html.py content-hash cache-busts the `?v=` query
- `src/analysis/focus_themes.py` — theme dictionary clustering
- `src/utils/db.py` — async DB client over the restricted Edge Function
- `data/theme_dictionary.json` — main/sub industry hierarchy (ticker-centric, TW only)
- `supabase/functions/db-proxy-public/` — Edge Function source (35-pattern allowlist)
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
