# IIA System — 兩 repo 全景

> **這份檔案位於 public stockgg repo 但描述整個系統**。
> 任何 session 開頭都應先讀這份，不論你在哪個 repo。

## 兩個 repo 的職責

| 屬性 | `stockgg`（這個 repo） | `StockGG-ingest`（私有 repo） |
|---|---|---|
| **本機路徑** | `~/Desktop/Stock` | `~/Desktop/StockGG-ingest` |
| **GitHub** | `https://github.com/GarlicChives/stockgg`（public） | `https://github.com/GarlicChives/StockGG-ingest`（private） |
| **角色** | thin presentation layer | 全資料 pipeline + admin UI |
| **能讀的 DB 欄位** | 9 個 allowlist 過的欄位 only | 全部 |
| **DB 認證** | `SUPABASE_ANON_KEY`（legacy JWT） | `SUPABASE_SERVICE_ROLE_KEY` |
| **DB 路徑** | `db-proxy-public` Edge Function | `db-proxy` Edge Function |
| **可跑 LLM** | ❌ 完全不能（無 API key） | ✅ Gemini 2.5 Flash + Ollama fallback |
| **可爬蟲** | ❌ 沒有任何爬蟲程式碼 | ✅ Playwright + RSS + yfinance |
| **launchd 排程** | ❌ 完全沒有 | ✅ 8 個 active jobs（見下） |
| **CI/CD** | GitHub Actions → wrangler → Cloudflare Workers | ❌ 沒有 CI |
| **觸發者** | cron + repo dispatch（從私有 repo） | launchd + Mac 開機 |

## 架構圖

```
                       ┌─────────────────────┐
                       │   Supabase DB       │
                       │ mnseyguxiiditaybpfup│
                       └──┬───────────────┬──┘
            service_role  │               │  anon (JWT)
            [db-proxy]    │               │  [db-proxy-public + 9 allowlist]
                          │               │
              ┌───────────▼──┐     ┌──────▼──────────┐
              │ StockGG-     │     │ stockgg         │
              │  ingest      │     │ (public)        │
              │ - crawlers   │     │ - generate_html │
              │ - Whisper    │     │ - focus_themes  │
              │ - Gemini     │     │ - 投資免責 footer│
              │ - admin UI   │     │                 │
              │ - launchd    │     │                 │
              │ - gh dispatch│─────▶ webhook trigger │
              └──────────────┘     └────────┬────────┘
                                            │
                                   ┌────────▼────────┐
                                   │ Cloudflare      │
                                   │ Workers         │
                                   │ stockgg.v...    │
                                   └─────────────────┘
```

## 排程（全部在 StockGG-ingest，本機 Mac launchd）

| 時間 (TW) | Job | Script | 用途 | webhook? |
|---|---|---|---|---|
| 開機 + 30 分 interval | `com.iia.catchup` | `scripts/catchup.py` | 開機補漏 missed jobs | ❌ |
| 每小時 :30 | `com.iia.podcast-crawl` | `src/crawlers/podcasts.py` | RSS + Whisper 轉錄 | ❌ |
| 04:30 | `com.iia.us-rankings` | `scripts/fetch_rankings.py us` | 美股成交值前 30（盤後） | ❌ |
| 07:00 | `com.iia.podcast-backfill` | `scripts/podcast_backfill.py` | 補 refine 失敗集 | ❌ |
| 07:30 | `com.iia.daily-briefing` | `scripts/daily_briefing.py` | 市場數據 + 日報 + theme dict | ✅ Step 9 |
| 17:30 | `com.iia.tw-rankings` | `scripts/fetch_rankings.py tw` | 台股成交值前 30（盤後） | ❌ |
| 18:00 | `com.iia.market-notes` | `scripts/run_market_notes.py` | 跨來源議題 + earnings preview | ✅ 結尾 |
| 21:00 | `com.iia.article-crawl` | `src/crawlers/(many)` | 訂閱專欄爬蟲（5 來源） | ❌ |
| 23:00 | `com.iia.market-notes` | （同上） | 同上，覆蓋當日完整 podcast | ✅ 結尾 |

**公開站觸發**：webhook（即時）+ 後備 cron（07:30 / 18:15 / 23:15 TW）。

## 資料來源（全部在 StockGG-ingest）

### 訂閱專欄（需 login 的 Playwright 爬蟲）
- macromicro（財經 M 平方）
- vocus 韭菜王
- statementdog（財報狗）
- investanchors
- pressplay（財經捕手）

連線方式：Playwright **persistent context**（`src/utils/browser.py`），
自己的 **headful** Chromium + 持久 profile `.crawler-profile/`（gitignored）。
**不再用 Chrome CDP port 9222**。

為什麼 headful：MacroMicro 等站有 Cloudflare，**所有 headless 模式都被擋**
（含 `--headless=new`，2026-05-14 驗證）。只有 headful 能過。排程時視窗
被丟到螢幕外（`--window-position=-2400,-2400`），使用者看不到。除錯時
`CRAWLER_VISIBLE=1` 可叫出視窗。

首次登入見 `StockGG-ingest/docs/crawler-login.md`：
`uv run python -m src.utils.browser` 跑一次手動登入 5 站
（InvestAnchors 用 email/密碼，非 Google SSO）。cookie 過期 → 重跑該指令。

### Podcast（RSS + Whisper 轉錄）
- gooaye 股癌、macromicro、chives_grad 韭菜畢業班、stock_barrel、zhaohua 兆華、statementdog podcast

### 公開市場資料
- yfinance（指數、Top 30 trading value、analyst consensus、earnings dates）
- TWSE / TPEX（台股 ranking）
- federalreserve.gov + hard-coded（FOMC / 大型 conference 日曆）

## DB Schema 快速參考

| Table | 主要欄位 | 寫入者 | 公開可讀？ |
|---|---|---|---|
| `articles` | id, source, title, content, refined_content, tickers, content_tags, embedding | StockGG-ingest | ❌（只有 service_role） |
| `analysis_reports` | report_date, raw_response, market_notes_json | StockGG-ingest（Gemini 產出） | ✅ |
| `market_snapshots` | symbol, close_price, change_pct, snapshot_date, extra | StockGG-ingest | ✅ |
| `trading_rankings` | ticker, name, trading_value, change_pct, rank_date, market, extra | StockGG-ingest | ✅ |
| `catalyst_events` | id, event_date, event_type, ticker, market, title, importance, preview_text | StockGG-ingest | ✅ |
| `watchlist` | id, ticker, market, name, ...（thesis 欄位於 feature 3 移除時 drop） | 預留 | — |

## 公開 repo 能執行的 SQL（9 個 allowlist 規則）

完整見 `supabase/functions/db-proxy-public/index.ts`。任何不在這 9 個內的 query → HTTP 403。
要擴充就改那個 Edge Function 並重新 deploy。

## 啟動 / 操作指南

### StockGG-ingest（私有，本機）
```bash
cd ~/Desktop/StockGG-ingest
uv sync
# 任何 script：
uv run scripts/daily_briefing.py
uv run src/crawlers/podcasts.py --incremental
# Admin UI（localhost only）：
uv run uvicorn admin.main:app --port 8765
open http://localhost:8765
# 看 8 個 launchd job 狀態：
launchctl list | grep com.iia
```

### stockgg（公開，本機 dev / GitHub CI 部署）
```bash
cd ~/Desktop/Stock
uv sync
uv run python scripts/generate_html.py
open docs/index.html
# 手動觸發 CI 部署：
gh workflow run "Publish daily site" --ref main
```

### 跨 repo：私有 → 公開 webhook
私有 repo 的 `daily_briefing.py` 跑完會自動執行：
```bash
gh workflow run "Publish daily site" --repo GarlicChives/stockgg --ref main
```
（透過 `~/Desktop/StockGG-ingest/src/utils/publish_trigger.py`）

## 關鍵檔案地圖

### stockgg（10 個檔案以內，極簡）
- `scripts/generate_html.py` — 單檔 HTML 渲染器
- `src/analysis/focus_themes.py` — 題材叢集（純 Python，不碰 LLM）
- `src/utils/db.py` — async DB client，用 `db-proxy-public` + anon key
- `data/theme_dictionary.json` — 226 個主題的人工字典
- `supabase/functions/db-proxy-public/index.ts` — Edge Function（9-pattern allowlist）
- `.github/workflows/market_briefing.yml` — 唯一一個 workflow

### StockGG-ingest（大部分原 Stock-test 內容）
- `src/crawlers/` — 5 個訂閱來源 + podcasts.py（Whisper subprocess + lock）
- `src/news/` — market_data, tw/us_rankings, catalyst_calendar
- `src/analysis/` — daily_report, market_notes, earnings_preview, focus_themes
- `src/theme/` — Tavily search + Gemini classifier
- `src/utils/` — db, refine, publish_trigger, api_logger, browser
- `src/prompts/` — 6 個 prompt（daily_report, market_notes, refine_article, refine_podcast, theme_classifier, earnings_preview）
- `scripts/daily_briefing.py` — 7:30 主編排
- `scripts/run_market_notes.py` — 18:00/23:00 主編排
- `scripts/transcribe_one.py` — Whisper subprocess worker（解過 OOM）
- `admin/` — FastAPI + HTMX localhost-only
- `launchd/` — 8 個 active plist + 1 個 disabled admin-ui plist

## Gotchas（容易踩坑）

1. **`src/utils/db.py` 在兩個 repo 裡內容不同**：
   - stockgg：`SUPABASE_ANON_KEY` + `db-proxy-public`
   - StockGG-ingest：`SUPABASE_SERVICE_ROLE_KEY` + `db-proxy`
   不要把其中一邊 `git pull` / copy 到另一邊。

2. **公開 repo 加新查詢必須同步擴 allowlist**：在 `generate_html.py` 新增任何 `conn.fetch(...)` 後，必須在 `supabase/functions/db-proxy-public/index.ts` 的 `ALLOWED` 集合加入該 SQL 的 whitespace-normalized lowercase 版本，並 `supabase functions deploy db-proxy-public --project-ref mnseyguxiiditaybpfup`。否則 CI 會 403。

3. **launchd PATH 必須含 `/opt/homebrew/bin`**（Apple Silicon）：podcast-crawl 跑 ffmpeg 用得到。所有 plist 已修；新增 plist 別忘加。

4. **Whisper 必須 subprocess 隔離**：`scripts/transcribe_one.py` 是專門隔離 MLX 記憶體洩漏的 worker，不要把它 inline 回 podcasts.py。背景見 commit `dc0634ad`。

4b. **訂閱爬蟲用 persistent context（headful），不是 CDP**：`src/utils/browser.py` 的 `connect_browser()` 回傳 `BrowserContext`（不是 `Browser`）。呼叫端是 `context = await connect_browser(p)` → ... → `await context.close()`。**必須 headful**——Cloudflare 擋所有 headless（含 new-headless）；排程時視窗在螢幕外。不要為了「乾淨」改回 headless，會被 CF 擋死。profile 在 `.crawler-profile/`。登入失效（logs 出現大量 `[LOCKED]`）→ 跑 `uv run python -m src.utils.browser` 重新登入。

5. **單一實例鎖**：podcast-crawl 有 `single_instance()` flock 防 launchd 並行觸發。新加排程若會大量吃記憶體請同樣加。

6. **Gemini 免費額度 50 RPD**：daily-briefing + market-notes ×2 + thesis（已移除） + earnings_preview 共 ~5-15 call/day，仍在範圍內。密集 debug 一天可能耗盡，隔天自動恢復。

7. **公開 repo 改 prompt 沒用**：所有 prompt 都在 StockGG-ingest 跑。改 stockgg 的什麼也不會生效（而且 stockgg 根本沒 prompt 檔案了）。

## 異動觸發表（commit 前必查）

下表列出**哪種改動必須同步更新哪份 doc**。沒有更新的 commit 會被 pre-commit
hook 拒絕（見 `hooks/pre-commit`）。緊急想 bypass 用 `git commit --no-verify`。

| 你改了什麼 | 必須同步更新 | 為什麼 |
|---|---|---|
| `launchd/com.iia.*.plist`（任何排程） | SYSTEM.md「排程」表 | 排程是系統最不透明的部分；改了不寫等於黑箱 |
| `src/utils/db.py`（DB 連線方式 / 認證 key） | SYSTEM.md「兩 repo 職責表」 + 對應 repo 的 CLAUDE.md | 兩 repo db.py 內容不同是最容易跨抄出錯的點 |
| `src/utils/publish_trigger.py`（webhook 目標） | SYSTEM.md「資料流」+ 公開 repo `.github/workflows/*.yml` 對照 | webhook 與 cron 互為備援，斷裂時無法救援 |
| `.github/workflows/*.yml`（公開 repo CI） | SYSTEM.md「排程」表的 cron 行 + 公開 repo 的 CLAUDE.md | CI 觸發時機改了影響公開站更新延遲 |
| `supabase/functions/db-proxy-public/index.ts` (allowlist) | SYSTEM.md「公開 repo 能執行的 SQL」section | allowlist 與 generate_html.py 同步是法律隔離的核心 |
| `scripts/daily_briefing.py` / `run_market_notes.py`（步驟順序、新加 step） | SYSTEM.md「資料流」+ 對應 repo 的 CLAUDE.md | 主編排是系統脈絡的骨幹 |
| `src/prompts/*.md`（私有 repo）| 私有 repo 的 CLAUDE.md「prompt list」section（如果有變更張數） | 新增/刪除 prompt 等於新增/移除分析功能 |
| `pyproject.toml`（新增/移除 deps） | 對應 repo 的 CLAUDE.md「技術棧」描述 | 依賴變動會影響跑得起來 |
| 新增 `src/crawlers/*.py`（私有 repo） | SYSTEM.md「資料來源」list + 加進對應的 launchd plist | 新爬蟲不寫進 SYSTEM 等於不存在 |
| `.env.example`（新增/移除環境變數） | 對應 repo 的 CLAUDE.md「本地操作」 + 公開 repo workflow yaml 的 env block | 缺 env 跑起來會炸但訊息隱晦 |

**判讀流程**：
1. `git diff --cached --name-only` 看 staged 檔
2. 對照上表，任一行命中 → 把對應 doc 也加進這個 commit
3. 多檔同步性改動視為一個結構變更，doc 與 code 必須同一個 commit

---

## 歷史

完整 migration 紀錄見 `~/Desktop/Stock/migration/PROGRESS.md`。Repo split 完成於 2026-05-13。
