# Investment Intelligence Analyst (IIA) — 系統架構

自動化整合台美股市場數據、訂閱專欄與 Podcast，產出每日投資簡報並發布到 GitHub Pages。

詳細 Prompt 內容見 [PROMPTS.md](./PROMPTS.md)。

---

## 1. 專案目標與資料流

```
┌─────────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ 訂閱專欄/Podcast│───▶│ 精煉(Gemini/ │───▶│ 跨來源議題   │───▶│  HTML 發布   │
│   爬蟲           │    │  Ollama)     │    │ + 焦點題材  │    │ (GitHub Pages)│
└─────────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                                                  ▲
┌─────────────────┐    ┌──────────────┐           │
│ 市場數據(yf,TWSE,│───▶│ 主題字典分類 │───────────┘
│  TPEX)          │    │ (Tavily+LLM) │
└─────────────────┘    └──────────────┘
```

每日輸出：[https://garlicchives.github.io/Stock-test/](https://garlicchives.github.io/Stock-test/)

---

## 2. 技術棧

| 類別 | 選用 |
|---|---|
| 語言 | Python 3.12+ |
| 套件管理 | `uv` |
| 資料庫 | Supabase PostgreSQL（透過自建 Edge Function `db-proxy` 走 HTTPS port 443） |
| DB adapter | `src/utils/db.py` — asyncpg 相容介面 |
| 向量檢索 | pgvector + sentence-transformers `paraphrase-multilingual-mpnet-base-v2`（768 維） |
| 瀏覽器自動化 | Playwright（連接使用者 Chrome `--remote-debugging-port=9222`） |
| AI 精煉/分析 | Gemini 2.5 Flash / Flash-Lite + Ollama qwen2.5:7b（fallback） |
| 主題搜尋 | Tavily Search API（免費 1000 次/月） |
| 排程 | macOS launchd（本機）+ GitHub Actions（雲端備份） |
| 通知 | Telegram Bot（待設定） |

---

## 3. 每日排程總覽（時間皆為台灣時間）

### 本機 launchd（5 個 job，全部已 load）

| 時間 | Job | 腳本 | 用途 | 相依輸入 |
|---|---|---|---|---|
| 06:00 | `com.iia.podcast-crawl` | `src/crawlers/podcasts.py --incremental` | RSS 抓取 + Whisper 轉錄 + Gemini 精煉 | RSS feeds（5 個 podcast 來源） |
| 07:00 | `com.iia.podcast-backfill` | `scripts/podcast_backfill.py` | 補跑 `content_tags` 為空的集數 | 06:00 的產出 |
| 07:30 | `com.iia.daily-briefing` | `scripts/daily_briefing.py` | 主流程（Step 1-8，見 §4） | articles + 06:00/07:00 podcast |
| 08:00 / 21:00 | `com.iia.article-crawl` | `scripts/run_all_crawlers.py --incremental` | 5 個訂閱專欄爬蟲（**需 Chrome 9222**） | Chrome session cookies |
| 開機 / 連網 / 每 30 分 | `com.iia.catchup` | `scripts/catchup.py` | 補跑漏掉的任務（DB 驅動，冪等） | DB 上次執行時間 |

`catchup` 觸發條件：`RunAtLoad: true`、`WatchPaths: /private/var/run/resolv.conf`、`StartInterval: 1800`。article-crawl 因需 Chrome 不會被補跑。

> **⚠️ launchd PATH 規則（Apple Silicon 必讀）**
> 所有 plist 的 `EnvironmentVariables/PATH` 必須包含 `/opt/homebrew/bin`：
> ```xml
> <key>PATH</key>
> <string>/opt/homebrew/bin:/Users/edward.song/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
> ```
> Apple Silicon Homebrew 安裝路徑為 `/opt/homebrew/bin`（Intel 為 `/usr/local/bin`）。
> launchd 不繼承使用者 shell 的 PATH，缺少此路徑會導致 `ffmpeg`、`npx` 等工具 not found，
> Podcast Whisper 轉錄會靜默 fallback 到 show notes（短內容），而且不報錯。
> 新增 plist 時務必照此格式填寫，否則重新 load 後才會生效。

### GitHub Actions（雲端備援）

| 觸發 | Workflow | 步驟 |
|---|---|---|
| Cron `30 23 * * 1-5`（07:30 TW 平日）+ `workflow_dispatch` | `.github/workflows/market_briefing.yml` | `daily_briefing.py` → `generate_html.py` → push `docs/index.html` |

⚠️ **本機與 CI 同時跑 07:30** — 兩邊都會 push 到 main。launchd 跑得快通常會贏；CI 偶爾 push 失敗會被拒（`non-fast-forward`）但不影響資料正確性。若要避免衝突可考慮：（a）關掉 CI cron 改純 backup、（b）讓 CI 只在 launchd 沒跑成功時觸發（需額外的 sentinel 檔）。

---

## 4. 主題字典自動發現（Step 5.5）

每天 daily_briefing 跑 `build_theme_dictionary.py`：

```
TW+US Top30 ── (per-ticker 30天快取) ──┐
                                       │
  cache miss ──► Tavily 搜尋 ──► snippets ──► Gemini 分類
                                                   │
                            雙輸出 ┌──── matched: 對到字典已有 theme ──► upsert 股票
                                   └──── new_themes: 字典沒有的概念股
                                                   │
                          dedup（去除「概念股」、標點，名字正規化比對）
                                                   │
                                ├─ 對到既有 → 加進 matched
                                └─ 真新 → 寫入字典（auto_created=true）
                                                   │
                                            cache.set(ticker, ids)
```

**設計理念：** 字典是會自我成長的資產。當媒體把某股標為新概念股（如「機器人概念股」「量子運算概念股」），系統會自動把該主題加進字典並收錄該股，不需人工逐一新增。

**品質控制：** `auto_created: true` 旗標讓使用者隨時撈出最近自動建立的 theme review／合併／刪除：
```bash
jq '.themes[] | select(.auto_created==true) | {id, name, auto_created_at}' data/theme_dictionary.json
```

## 5. 熱門題材選取邏輯（焦點股頁面）

對應程式：`src/analysis/focus_themes.py:detect_clusters()`，顯示：`scripts/generate_html.py:build_focus_html()`

### 入選條件

**台美股合計前 30（成交值排名）中，同一題材字典內成員股票達 ≥ 2 檔，該題材即為熱門題材。**

| 參數 | 位置 | 預設 | 說明 |
|---|---|---|---|
| `MIN_VOLUME` | `focus_themes.py` | `2` | 最低入選成員數 |
| `MIN_SCORE` | `focus_themes.py` | `1.0` | 文章關鍵字確認門檻（僅影響 badge） |
| `PRIMARY_DAYS` | `focus_themes.py` | `7` | 近期文章定義（天數，加權 ×2） |

### 顯示規則

熱門題材 tab 內分兩個子分頁：

| 子分頁 | 對象 | 排序 |
|---|---|---|
| 台股題材 | 含台股焦點 | `tw_trading_value` 遞減 |
| 美股題材 | 含美股焦點 | `us_trading_value` 遞減 |

成交值三步驟累加：
1. **Step 1**（detect_clusters）：今日前 30 焦點股的市場成交值
2. **Step 2**（yfinance fetch 後）：加入前哨觀察股（`close × volume`）
3. **Step 3**（渲染時）：每個市場各自按累計成交值排序

### 強度 Badge

| 條件 | Badge |
|---|---|
| 任一成員關鍵字命中 + `primary_art_count ≥ 2` 或焦點 ≥ 2 | 強勢 |
| 其他有文章命中 | 觀察 |
| 無文章命中 | 量能輪動（純量價，無文章佐證） |

### 與 Step 5.5（自動發現）的互動

- 新建立的 auto_created theme 通常只有 1 支股票（剛被 LLM 分類進來那支）
- `MIN_VOLUME=2` 表示要等到字典裡同族群第 2 支也進入前 30 成交值，這個 theme 才會出現在熱門題材頁
- 若想立刻看到單檔成立的小眾題材：人工編輯 `theme_dictionary.json` 把同族群其他股票加進 `tw_stocks` / `us_stocks`，下次 generate_html 就會顯示

---

## 6. `daily_briefing.py` 執行 DAG

```
Step 1: market_data       → market_snapshots
Step 2: us_rankings       → trading_rankings (US)
Step 3: tw_rankings       → trading_rankings (TW)   [TWSE + TPEX 合併]
        ↓
Step 4: daily_report      → analysis_reports.raw_response       [Gemini 2.5 Flash]
Step 5: market_notes      → analysis_reports.market_notes_json  [Gemini 2.5 Flash]
Step 5.5: build_theme_dict → data/theme_dictionary.json          [Tavily + Gemini Flash-Lite]
Step 6: cleanup_old_data  (articles >180d / podcasts >30d / news >180d)
Step 7: generate_html     → docs/index.html
Step 8: api_cost_check    → 印出 logs/api_usage.jsonl 的成本摘要
```

`generate_html.py` 自身的子流程（純讀 DB → 組 HTML）：抓 analysis_reports / market_snapshots / trading_rankings / articles → `focus_themes.detect_clusters()` 偵測題材叢集 → yfinance 補齊 watch 標的成交值與漲跌 → 渲染 3 分頁（市場行情 / 焦點股 / 股市筆記）→ 寫 `docs/index.html`。

### 冪等性與防呆

同一天重跑 `daily_briefing.py` 不會汙染資料：

| Step | 重跑行為 | 機制 |
|---|---|---|
| 1-3 市場/成交值 | 重新抓取覆蓋 | `DELETE WHERE rank_date=$1` 後 INSERT，不會累積過時 rank |
| 4 daily_report（Gemini，付費） | **跳過** | 檢查 `analysis_reports.raw_response IS NOT NULL` |
| 5 market_notes（Gemini，付費） | **跳過** | 檢查 `analysis_reports.market_notes_json IS NOT NULL` |
| 5.5 theme_dict（Tavily+Gemini，付費） | 全部跳過 | per-ticker 30 天快取（`src/theme/cache.py`）；快取期間不重打 API |
| 6 cleanup | 重新執行 | 冪等的 `DELETE ... WHERE created_at < NOW() - INTERVAL` |
| 7 generate_html | 重新生成 | 純讀 DB，無副作用 |
| 8 cost report | 重新印 | 純讀 jsonl |

**強制重產**（改了 prompt 或 model 需要重新呼叫 Gemini）：
```bash
uv run scripts/daily_briefing.py --force            # 強制重跑 Step 4 + 5
uv run scripts/daily_briefing.py --skip-fetch --force # 跳過資料抓取，只重產 AI 部分
```

`--force` 不影響 5.5；要清空主題字典快取另用 `uv run scripts/build_theme_dictionary.py --reset-cache`。

---

## 7. AI 模型使用矩陣

| 呼叫點 | 模型 | temperature | maxOutputTokens | 用途 | 對應 prompt |
|---|---|---|---|---|---|
| `src/utils/refine.py:_gemini_refine` (article) | gemini-2.5-flash-lite | 0 | 8000 | 文章精煉、標 tags | `refine_article.md` |
| `src/utils/refine.py:_gemini_refine` (podcast) | gemini-2.5-flash-lite | 0 | 8000 | Podcast 逐字稿結構化 | `refine_podcast.md` |
| `src/utils/refine.py:_refine_ollama` | qwen2.5:7b（本機 Ollama） | 0 | — | 文章精煉 fallback（podcast 不用） | 同上兩個 |
| `src/theme/classifier.py:GeminiClassifier` | gemini-2.5-flash-lite | 0 | 512 | 將 Tavily 搜尋摘要分類到主題 ID | `theme_classifier.md` |
| `src/analysis/daily_report.py:_gemini_http` | gemini-2.5-flash | 0.3 | 8192 | 每日總經 + 多空判斷 | `daily_report.md` |
| `src/analysis/market_notes.py:_gemini_http` | gemini-2.5-flash | 0.2 | 8192 | 跨來源共同議題擷取 JSON | `market_notes.md` |

**Routing：**
- Podcast 精煉：Gemini only（qwen 格式遵從度不足）
- Article 精煉：Ollama 優先 → Gemini fallback（Ollama 沒跑時）
- Gemini 免費額度約 50–100 RPD；密集 debug 當天可能耗盡（隔天恢復）

成本紀錄：`logs/api_usage.jsonl`（`src/utils/api_logger.py`），每次呼叫一行 JSON。

---

## 8. 資料庫 Schema 概覽

| 資料表 | 主要欄位 | 用途 |
|---|---|---|
| `articles` | source, title, content, **refined_content**, **content_tags**, **embedding** (vec768), tickers, published_at | 文章 + Podcast 逐字稿；`content_tags='{}'` 代表精煉失敗或無投資內容 |
| `market_snapshots` | symbol, close_price, change_pct, snapshot_date, extra(jsonb) | 每日指數 + VIX + Fear&Greed |
| `trading_rankings` | rank_date, market(US/TW), rank, ticker, name, trading_value, change_pct, is_limit_up_30m | 每日成交值前 30 |
| `analysis_reports` | report_date, raw_response, **market_notes_json**(jsonb) | Gemini 日報 + 跨來源議題 |
| `news_items` | （預留） | 每日金融新聞 |
| `watchlist` | （預留） | 使用者追蹤標的 |

---

## 9. 環境變數清單

| 變數 | 必要 | 用途 | 設定位置 |
|---|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | DB Edge Function 認證 | `.env`（本機）+ GitHub Secret |
| `GOOGLE_API_KEY` | ✅ | Gemini（精煉、日報、議題、分類） | 同上 |
| `TAVILY_API_KEY` | ✅ | 主題字典搜尋 | 同上 |
| `HF_TOKEN` | 選用 | Hugging Face token；下載 sentence-transformer 避開 rate limit | `.env` |
| `CHROME_DEBUG_PORT` | 選用 | 預設 9222 | `.env` |
| `ANTHROPIC_API_KEY` | （未來） | 設定後可遷移精煉/分析至 Claude | — |

**注意：** `DATABASE_URL` 為棄用變數；HTTPS 遷移後不再使用，舊 `.env` 內可保留但不影響運作。

---

## 10. 主要程式檔案地圖

```
scripts/
├── daily_briefing.py         # 8 步驟主流程
├── generate_html.py          # 純 DB → HTML
├── build_theme_dictionary.py # Tavily + Gemini 分類，每日 Step 5.5
├── podcast_backfill.py       # 補跑未精煉的 podcast
├── catchup.py                # 開機/連網補漏
└── run_all_crawlers.py       # 5 個專欄爬蟲依序執行

src/
├── crawlers/                 # macromicro / vocus / statementdog / investanchors / pressplay / podcasts
├── news/                     # market_data, us_rankings, tw_rankings (TWSE + TPEX 合併)
├── analysis/
│   ├── daily_report.py       # Gemini 日報
│   ├── market_notes.py       # Gemini 跨來源議題
│   └── focus_themes.py       # 焦點題材叢集偵測（雙閘門）
├── theme/
│   ├── search.py             # TavilyProvider
│   ├── classifier.py         # GeminiClassifier
│   └── cache.py              # 30-day TTL
├── prompts/                  # ★ 5 個 prompt 檔（編輯不需 commit code）
│   ├── __init__.py           # load() / render()
│   ├── refine_article.md
│   ├── refine_podcast.md
│   ├── theme_classifier.md
│   ├── daily_report.md
│   └── market_notes.md
└── utils/
    ├── db.py                 # asyncpg 相容 adapter（HTTPS Edge Function）
    ├── refine.py             # 精煉 pipeline
    └── api_logger.py         # 成本紀錄
```

---

## 11. 已知限制與待辦

- ✅ `ffmpeg` 已安裝（`/opt/homebrew/bin/ffmpeg`），Whisper 轉錄正常運作
- ⏳ Telegram Bot token — daily_briefing 推播
- ⏳ `ANTHROPIC_API_KEY` — 遷移精煉至 Claude Haiku、分析至 Sonnet，解決 Gemini 429
- ⚠️ Article crawl 需 Chrome 在 9222；開機後需手動啟動（或用 `.chrome-profile/` launchd job）
- ⚠️ Podcast 廣告/問答集數精煉結果為 NONE，不顯示在頁面（過濾條件 `content_tags != '{}'`）
- ⚠️ launchd 與 GitHub Actions 兩邊同時 07:30 跑，偶有 push 競爭（不致資料錯誤）
