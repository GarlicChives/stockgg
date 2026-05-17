# Project: stockgg (public daily-briefing site)

> **新 session 開頭請先讀 `~/Desktop/StockGG-ingest/SYSTEM.md`** —— 那是兩個 repo
> 的全景，包含資料流、排程、各 repo 職責、踩坑點。SYSTEM.md 實體放在私有的
> StockGG-ingest repo（因含爬蟲 / 訂閱站營運細節，不進公開 repo）；本檔只覆蓋
> 本 repo 的 do/don't。

## 本 repo 角色

Thin presentation layer。只渲染 HTML + 部署 Cloudflare Workers。
資料攝取、AI 分析、爬蟲全部在 companion repo `StockGG-ingest` 跑
（本機 `~/Desktop/StockGG-ingest`，私有 GitHub repo）。

## 嚴格 guardrail

| 規則 | 為什麼 |
|---|---|
| ❌ **不要引入** `SUPABASE_SERVICE_ROLE_KEY` 到本 repo | service_role 是私有 repo 專用；本 repo 用 anon |
| ❌ **不要呼叫** Gemini / OpenAI / 任何 LLM API | 公開 repo 沒任何 LLM key，也不該有 |
| ❌ **不要爬任何網站** | 法律隔離邊界，原始內容只能在私有 repo |
| ❌ **不要在這裡跑 LLM-generated 分析** | 分析是私有 repo 的事，這裡只讀已存的結果 |
| ✅ **新增或調整 query 就同步擴 allowlist** | 改 `supabase/functions/db-proxy-public/index.ts` 的 `ALLOWED`（新增條目，或修改既有 SQL 樣板 —— normalize-比對是 exact match，整段改才會通過），然後跑 `bash scripts/deploy_db_proxy_public.sh`（wrapper 內含 Supabase CLI auth liveness probe，token 失效會給繁中提示）。不擴/不 redeploy 會 CI 403 |
| ✅ **不確定查詢能不能跑時，本機跑 generate_html.py 看 403** | 是最快的 sanity check |

## 關鍵檔案

- `scripts/generate_html.py` — 單檔 HTML 渲染（~2600 行,所有頁面邏輯 + 內嵌 CSS/JS）
- `src/analysis/focus_themes.py` — 題材叢集（純 Python）
- `src/utils/db.py` — async DB client（用 `SUPABASE_ANON_KEY` + `db-proxy-public`）
- `data/theme_dictionary.json` — statementdog 主產業 / 子產業階層字典（2026-05 改 schema:ticker-centric `stocks` 物件,純台股;由 ingest 端 `scrape_statementdog_industries.py` 產生再 sync 到本 repo）
- `supabase/functions/db-proxy-public/index.ts` — Edge Function 含 SQL allowlist（目前 12 條）
- `.github/workflows/market_briefing.yml` — render + deploy（07:30 / 18:15 / 23:15 TW cron + repository_dispatch）。`concurrency: publish-daily-site` 同 workflow 排隊不互相取消;commit-and-push step 含 `-X ours` rebase retry x3,避免本地 dev push 與 bot 撞 race
- `docs/index.html` — 渲染輸出(generate_html.py 寫入,bot CI push)
- `docs/history.json` — chart modal 用的歷史 payload(theme_history + 大盤指數,~800KB),由 `generate_html.py` 一併寫出,前端 lazy fetch
- `wrangler.jsonc` — `assets.directory: docs` → Workers 整個 docs/ 當靜態 asset 服務

## 前端架構速覽

- **單頁 SPA + tab**(市場行情 / 熱門題材 / 焦點排行 / 股市筆記),純 vanilla JS,所有 state 內嵌
- **inline payload** 注入在 HTML script tag 內:
  - `IIA_CLUSTERS.sub`(子產業 cluster + focal ticker + per-ticker chg/yld)
  - `IIA_RADAR`(每檔 ticker 的 5 維 metric 與全焦點股平均,modal radar chart 用)
  - `artModalData`(各 ticker 的 analyst consensus + 公司介紹 HTML 片段)
- **lazy fetch**:`history.json`(modal chart 開啟才 fetch),`unpkg lightweight-charts`(同上)
- **互動點**:
  - 廣泛概念股 chip 濾除(universal toggle)→ FLIP 動畫重排 cluster
  - cluster sort chip(TV / 平均漲跌 / 平均殖利率)→ 共用同 _recalcClusters
  - chart 時間粒度 chip(1M/3M/6M/1Y/ALL)→ 過濾 series 後 rebase to 100
  - modal radar chart(5 維 vs 焦點股平均)、CSV 下載、site search、share button

## 本地操作

```bash
uv sync
uv run python scripts/generate_html.py     # 重生 HTML
open docs/index.html                        # 本機檢視
gh workflow run "Publish daily site"        # 手動觸發 CI 部署
```

## Commit 前 checklist（自我審計）

每次 commit 之前 mental walk-through：

- [ ] 改動的檔案在 SYSTEM.md「異動觸發表」內嗎？是 → 同 commit 更新本 repo 的
  `.claude/CLAUDE.md` / `README.md`（hook 只認這兩個）。若該改動也需更新
  SYSTEM.md 的 section → SYSTEM.md 在 `StockGG-ingest` repo，**另開一次該 repo
  的 commit + push**
- [ ] 改了 `generate_html.py` 的 `conn.fetch/fetchrow/fetchval`？是 → 同步擴
  `supabase/functions/db-proxy-public/index.ts` 的 `ALLOWED`，並 redeploy
- [ ] 改了 CSS 或 HTML 結構？是 → 本機 `uv run python scripts/generate_html.py`
  + `open docs/index.html` 親眼看一次
- [ ] 改了 chart / lazy-load 相關?是 → 確認 `docs/history.json` 也一併
  regen(write 在 generate_html.py 末段),不要只 commit index.html
- [ ] 改了 SEO meta(og:title / description 等)?是 → 用 Twitter Card
  Validator / FB Debugger 看 preview 有沒有跑掉
- [ ] 在 Python fstring 內寫 JS 時 `\n` / `\r` / `\t` / `{`、`}` 都要雙
  反斜線或雙大括號(`\\n`、`{{`、`}}`),不然 Python 自己 escape 後
  JS 收到 broken pattern。pre-commit 不會抓,要本機 console 看
- [ ] Pre-commit hook 跑通沒？沒看到 ✋ 提醒就過了 = 改動非結構性

## 待辦

- [ ] Custom domain（Phase 4.4，買域名後）
