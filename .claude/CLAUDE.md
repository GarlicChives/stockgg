# Project: StockGG (public daily-briefing site)

## 角色

這個 repo 是 **thin presentation layer**。所有資料攝取、AI 分析、爬蟲都在
另一個 private repo `StockGG-ingest` 跑（user 的 Mac），結果寫入 Supabase。
這個 repo 從 Supabase 讀回分析結果並渲染 HTML，部署到 Cloudflare Workers。

## 技術棧

- **語言**: Python 3.12+，套件管理 uv
- **DB 連線**: `src/utils/db.py` httpx 走 Supabase Edge Function `db-proxy-public`，
  port 443 HTTPS only。**強制 9-pattern SQL allowlist**——這個 repo 任何 SQL
  都必須是 allowlist 裡列出的 SELECT，否則 Edge Function 回 403。
- **認證**: `SUPABASE_ANON_KEY`（legacy JWT format，安全暴露於 client 用）。
  **絕不**在這個 repo 引入 `SUPABASE_SERVICE_ROLE_KEY`——那把 key 只屬於私有 repo。
- **HTML 渲染**: 純 Python 字串組裝；`scripts/generate_html.py` 單檔
- **部署**: GitHub Actions → wrangler deploy → Cloudflare Workers (`stockgg`)
- **觸發**:
  - `workflow_dispatch`（手動）
  - cron `30 23 * * 1-5`（07:30 TW）、`15 10 * * *`（18:15 TW）、`15 15 * * *`（23:15 TW）
  - `repository_dispatch` type `analysis-updated`（私有 repo webhook 觸發）

## 可讀的 DB columns（allowlist 唯一允許的）

| Table | Columns |
|---|---|
| `analysis_reports` | `report_date`, `raw_response`, `market_notes_json` |
| `market_snapshots` | `symbol`, `close_price`, `change_pct`, `snapshot_date`, `extra` |
| `trading_rankings` | `ticker`, `name`, `trading_value`, `change_pct`, `extra`, `is_limit_up_30m`, `rank_date`, `market` |
| `catalyst_events` | `id`, `event_date`, `event_type`, `ticker`, `market`, `title`, `importance`, `preview_text` |

任何其他欄位（特別是 `articles.content`, `articles.refined_content`，podcast 逐字稿）
**這個 repo 技術上無法讀取**。如需擴充顯示內容，先在
`supabase/functions/db-proxy-public/index.ts` 的 ALLOWED 集合擴 query 並重新 deploy。

## 不在這個 repo 做的事

- 爬蟲（含 Playwright / Chrome CDP）
- Whisper / mlx-whisper podcast 轉錄
- Gemini / 任何 LLM 呼叫
- 寫入 DB
- launchd 排程

以上全部在 private repo `StockGG-ingest` 裡。

## 主要程式碼

- `scripts/generate_html.py` — 單檔 HTML 渲染器
- `src/analysis/focus_themes.py` — 題材叢集（純 Python，吃 `data/theme_dictionary.json`
  + rankings，不碰 LLM）
- `src/utils/db.py` — async DB client over `db-proxy-public`
- `data/theme_dictionary.json` — 人工維護的主題 → 個股 mapping
- `supabase/functions/db-proxy-public/index.ts` — Edge Function，allowlist enforcement
- `.github/workflows/market_briefing.yml` — 唯一一個 workflow（render + deploy）

## 待辦事項

- [ ] Custom domain（Phase 4.4，買域名後）
