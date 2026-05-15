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
| ✅ **要加新 query 就同步擴 allowlist** | 改 `supabase/functions/db-proxy-public/index.ts` 的 `ALLOWED`，然後跑 `bash scripts/deploy_db_proxy_public.sh`（wrapper 內含 Supabase CLI auth liveness probe，token 失效會給繁中提示）。不擴會 CI 403 |
| ✅ **不確定查詢能不能跑時，本機跑 generate_html.py 看 403** | 是最快的 sanity check |

## 關鍵檔案

- `scripts/generate_html.py` — 單檔 HTML 渲染（~1500 行，所有頁面邏輯）
- `src/analysis/focus_themes.py` — 題材叢集（純 Python）
- `src/utils/db.py` — async DB client（用 `SUPABASE_ANON_KEY` + `db-proxy-public`）
- `data/theme_dictionary.json` — 226 主題的人工字典
- `supabase/functions/db-proxy-public/index.ts` — Edge Function 含 SQL allowlist
- `.github/workflows/market_briefing.yml` — render + deploy（07:30 / 18:15 / 23:15 TW cron + repository_dispatch）

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
- [ ] Pre-commit hook 跑通沒？沒看到 ✋ 提醒就過了 = 改動非結構性

## 待辦

- [ ] Custom domain（Phase 4.4，買域名後）
