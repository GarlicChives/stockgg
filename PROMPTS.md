# Prompt 維護手冊

本專案所有 AI prompt 都抽到 `src/prompts/*.md`，runtime 由 `src/prompts/__init__.py` 的 `load()` / `render()` 讀檔。**修改 prompt 不需要重啟服務或重新部署，下次 launchd / CI 跑就會生效。**

模板語法：Python `string.Template`（`$var` 或 `${var}`）。Prompt body 可自由使用 `{}`（JSON 範例不需轉義）。

---

## 索引

| Prompt | 檔案 | 模型 | temp | 用途 |
|---|---|---|---|---|
| 文章精煉 | [`src/prompts/refine_article.md`](src/prompts/refine_article.md) | gemini-2.5-flash-lite / qwen2.5:7b | 0 | 過濾文章雜訊，輸出 TAGS + CONTENT |
| Podcast 精煉 | [`src/prompts/refine_podcast.md`](src/prompts/refine_podcast.md) | gemini-2.5-flash-lite | 0 | 結構化財經 podcast 逐字稿 |
| 主題分類 | [`src/prompts/theme_classifier.md`](src/prompts/theme_classifier.md) | gemini-2.5-flash-lite | 0 | 將 Tavily 摘要分類到主題 ID |
| 每日日報 | [`src/prompts/daily_report.md`](src/prompts/daily_report.md) | gemini-2.5-flash | 0.3 | 總經近況 + 國際股市 + 多空判斷 |
| 跨來源議題 | [`src/prompts/market_notes.md`](src/prompts/market_notes.md) | gemini-2.5-flash | 0.2 | 找出 ≥2 來源共同提及的議題（JSON） |

---

## 1. 文章精煉 — `refine_article.md`

- **呼叫點**：`src/utils/refine.py:_gemini_refine`（line 116）/ `:_refine_ollama`（line 158）
- **模型**：Gemini 2.5 Flash-Lite（首選 fallback）/ Ollama qwen2.5:7b（文章首選）
- **參數**：`temperature=0`, `maxOutputTokens=8000`
- **輸入截斷**：`CONTENT_TRUNCATE = 4000` chars
- **變數**：無（純 system prompt；user 訊息為 `標題：{title}\n\n{raw}`）
- **輸出格式**：
  ```
  TAGS: macro,stock           ← 從 macro/international/stock/supply_chain 選
  CONTENT:
  <條列重點>
  ```
  若無投資內容回 `NONE`。
- **修改注意**：
  - `TAGS:` / `CONTENT:` 字面標記不可改（`_parse_refine_response` 用 regex 解析）
  - `NONE` 字面字串是「不是投資內容」訊號，不可改成其他字
  - 4 個 valid tags 寫死在 `_VALID_TAGS = {"macro","international","stock","supply_chain"}`，新增 tag 要同步改 set

---

## 2. Podcast 精煉 — `refine_podcast.md`

- **呼叫點**：`src/utils/refine.py:_gemini_refine`（is_podcast=True）
- **模型**：Gemini 2.5 Flash-Lite **唯一**（qwen2.5:7b 格式遵從度不足，podcast 不允許 fallback）
- **參數**：`temperature=0`, `maxOutputTokens=8000`
- **輸入截斷**：`PODCAST_TRUNCATE = 16000` chars
- **變數**：無
- **輸出格式**：與 `refine_article.md` 同樣的 `TAGS: ... CONTENT: ...`，但 CONTENT 主體要分【市場話題】+【標的提及】兩塊，且話題用「（一）、（二）、…」標號。
- **修改注意**：
  - 顯示條件 `content_tags != '{}'`（廣告集回 NONE → tags 空陣列 → 不顯示）
  - 結構化欄位（【市場話題】、【標的提及】）目前未被機器解析，純人類閱讀；若改格式不會破壞流程，但 HTML 上呈現會跑掉

---

## 3. 主題分類 — `theme_classifier.md`

- **呼叫點**：`src/theme/classifier.py:GeminiClassifier._build_prompt`（line 54）
- **模型**：Gemini 2.5 Flash-Lite（`response_mime_type=application/json`）
- **參數**：`temperature=0`, `maxOutputTokens=512`
- **變數**：
  - `$snippets` — Tavily 搜尋回傳的 3 條摘要拼接（`\n---\n` 分隔）
  - `$theme_lines` — 字典每個 theme 渲染為 `- {id}: {keyword} ({name})`
- **輸出**：純 JSON 陣列，最多 10 個 theme ID，例：`["cowos_advanced_packaging", "hbm_memory"]`
- **修改注意**：
  - 「主要參與者」門檻是核心規則，影響字典精準度。放寬會把次要供應商塞進來，收緊會漏掉真正的受惠股
  - 程式有 markdown fence 容錯（`re.sub(r"^```[a-z]*\n?", ...)`），即使 Gemini 偶爾包 ` ```json ... ``` ` 也能解
  - 上限 10 個 — 改數字記得在 prompt 與 `data/theme_rules.md` 同步說明

---

## 4. 每日日報 — `daily_report.md`

- **呼叫點**：`src/analysis/daily_report.py:_build_prompt`（line 155）
- **模型**：Gemini 2.5 Flash
- **參數**：`temperature=0.3`, `maxOutputTokens=8192`
- **變數**（共 18 個）：
  | 變數 | 內容 |
  |---|---|
  | `$snap_date`, `$rank_date` | 市場資料/排行日期 |
  | `$sp500`, `$sp500_pct` | S&P500 收盤 + 漲跌% |
  | `$nasdaq`, `$nasdaq_pct` | NASDAQ |
  | `$sox`, `$sox_pct` | SOX |
  | `$topix`, `$topix_pct` | 東證 TOPIX |
  | `$kospi`, `$kospi_pct` | 韓股 KOSPI |
  | `$taiex`, `$taiex_pct` | 台股加權 |
  | `$vix`, `$yield10`, `$dxy`, `$dxy_pct`, `$fg` | VIX / 10Y / DXY / Fear&Greed |
  | `$us_lines` | US 前 30 多行字串（`#1 NVDA NVIDIA $30.0B +1.0%`） |
  | `$tw_lines` | TW 前 30 多行字串（含「漲停」標記） |
  | `$articles` | 近 7 天文章（最多 25 篇，每篇 800 字） |
- **輸出結構**（必須三個 `## `）：
  1. `## 總經近況`（100 字內）
  2. `## 國際股市`（條列）
  3. `## 綜合多空判斷`（短/中/長 + 關鍵風險）
- **修改注意**：
  - generate_html.py 的 `build_market_html` 用 markdown 解析輸出，三個 H2 標題不可改
  - `_fmt_pct(p)` 已預先把 None/正負號處理好（如 `+1.50%` 或 `N/A`），prompt 內變數收到的就是格式化後字串
  - 如果改成更省 token 的格式，要同步調整 `_load_market_context` 與顯示端

---

## 5. 跨來源議題 — `market_notes.md`

- **呼叫點**：`src/analysis/market_notes.py:_build_prompt`（line 71）
- **模型**：Gemini 2.5 Flash（`thinkingConfig.thinkingBudget=0`，關 thinking 省 token）
- **參數**：`temperature=0.2`, `maxOutputTokens=8192`
- **變數**：
  - `$lookback_days` — 預設 7（`LOOKBACK_DAYS` 常數）
  - `$articles` — 近 N 天文章（最多 40 篇，每篇 800 字），格式 `[YYYY-MM-DD|來源中文名] 標題\n本文`
- **輸出**：JSON `{topics: [{topic, sentiment, sources, tickers, summary, key_points, articles}]}`
- **修改注意**：
  - **JSON schema 是合約**：`generate_html.py:build_notes_html` 直接用這些欄位渲染（topic / sentiment / sources / tickers / key_points）。改 key 名稱會破壞 HTML
  - 最少 2 個 sources 是核心過濾邏輯，放寬會出現「只有股癌講過」的議題
  - 標的格式 `公司名(代號)` 與 `TICKER(US)` 是 HTML 端用 regex 抓 ticker 的依據

---

## 修改流程

1. 直接編輯 `src/prompts/{name}.md`
2. 本機 sanity check：
   ```bash
   uv run python -c "from src.prompts import load, render; print(load('daily_report')[:200])"
   ```
3. 對應整合測試：
   - 改了 refine_*：`uv run scripts/podcast_backfill.py` 跑一兩集看輸出結構
   - 改了 theme_classifier：`uv run scripts/build_theme_dictionary.py --ticker 2330`
   - 改了 daily_report 或 market_notes：`uv run scripts/daily_briefing.py --skip-fetch` 略過資料抓取直接重產報告
4. commit `src/prompts/*.md`（純文字 diff 看得清楚）→ push
5. 下次 07:30（本機或 CI）會自動套用新 prompt

---

## 不在這裡的決策

- **模型選擇與 temperature/maxOutputTokens**：仍寫在程式碼（`GEMINI_MODEL` 常數、各 `_gemini_http` 內 generationConfig）。理由：模型/參數變動屬「程式行為」改動，需 PR review；prompt 文字改動屬「內容」，門檻較低
- **Routing**（哪個來源用哪個模型、fallback 順序）：在 `src/utils/refine.py:refine_content`
- **Truncate 上限**：`CONTENT_TRUNCATE` / `PODCAST_TRUNCATE` 常數
- **資料表 schema**：見 [ARCHITECTURE.md §6](./ARCHITECTURE.md#6-資料庫-schema-概覽)
