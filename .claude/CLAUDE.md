# Project: Investment Intelligence Analyst (IIA)

## 專案目標
建立一個自動化系統，整合訂閱專欄與全球金融資訊，提供具時效性的台美股投資建議。

## 技術棧
- **語言**: Python 3.12+
- **瀏覽器自動化**: Playwright（連接使用者現有已登入的 Chrome Session，via port 9222）
- **資料庫**: PostgreSQL（Supabase 雲端，跨裝置共用同一份資料）
  - DB 連線方式：`src/utils/db.py` asyncpg 相容 adapter，透過 Supabase Edge Function (db-proxy) 走 HTTPS port 443
  - 直接 asyncpg (port 5432) 已全面移除，公司 WiFi 完全可用
- **排程**: 本機 Mac（launchd）— 全部已 load，含開機補漏機制
- **AI 精煉**: Gemini 2.5 Flash（GOOGLE_API_KEY）主力；Ollama qwen2.5:7b 作為文章類 fallback
- **AI 分析**: Gemini 2.5 Flash（每日報告 + 跨來源議題分析）
- **Embedding**: sentence-transformers paraphrase-multilingual-mpnet-base-v2（本地，768 維）
- **套件管理**: uv
- **通知**: Telegram Bot（尚未設定，LINE Notify 已於 2025/3 停止服務）

> 注意：ANTHROPIC_API_KEY 尚未設定。目前所有 AI 任務均以 Gemini 2.5 Flash 執行。
> 待使用者在 console.anthropic.com 購買積分後，可將精煉/分析全面遷移至 Claude（Haiku 做批次精煉、Sonnet 做分析）。

## 訂閱專欄清單（M1 文章來源）
1. https://www.macromicro.me/ — 財經M平方（總經數據為主）
2. https://vocus.cc/salon/ChivesKing — 方格子 韭菜王
3. https://statementdog.com/industry_reports — 財報狗產業報告
4. https://investanchors.com/user/vip_contents/investanchors_index — 投資錨點 VIP
5. https://www.pressplay.cc/member/learning/projects/EFB905DAF7B44F479552E5F5D955A137/articles — PressPlay 財經捕手

## Podcast 來源（M1 音頻 RSS）
- `podcast_gooaye` — 股癌 Gooaye（soundon.fm RSS）
- `podcast_macromicro` — 財經M平方 podcast（soundon.fm RSS）
- `podcast_chives_grad` — 韭菜畢業班（soundon.fm RSS）
- `podcast_stock_barrel` — 股海飯桶 WilsonRice（soundon.fm RSS）
- `podcast_zhaohua` — 兆華與股惑仔（soundon.fm RSS）

Podcast 精煉路由：Gemini 2.5 Flash 唯一後端（qwen2.5:7b 格式遵從度不足，已停用 podcast fallback）。
顯示條件：`content_tags != '{}'`（有效 tags 代表 Gemini 結構化輸出成功）。

> ⚠️ ffmpeg 尚未安裝（`brew install ffmpeg`），Whisper 音頻轉錄目前 fallback 至 show notes（167 chars），
> 安裝後可獲取完整逐字稿（建議儘早安裝）。

## 每日排程（全部已 launchctl load）

| 時間 | launchd Job | 腳本 | 說明 |
|------|------------|------|------|
| 06:00 | `com.iia.podcast-crawl` | `src/crawlers/podcasts.py --incremental` | RSS 增量抓取 + Whisper 轉錄 + Gemini 精煉 |
| 07:00 | `com.iia.podcast-backfill` | `scripts/podcast_backfill.py` | 補齊 content_tags 為空的 podcast 集數（Gemini 精煉）|
| 07:30 | `com.iia.daily-briefing` | `scripts/daily_briefing.py` | 市場數據 + AI 報告 + 跨來源議題 + HTML rebuild |
| 08:00 & 21:00 | `com.iia.article-crawl` | `src/crawlers/run_all.py` | 文章增量爬取（需 Chrome port 9222）|
| 開機/連網/每30分 | `com.iia.catchup` | `scripts/catchup.py` | 補跑漏掉的任務（DB 驅動，冪等）|

### catchup.py 觸發條件
- `RunAtLoad: true` — 開機或 launchctl load 時
- `WatchPaths: /private/var/run/resolv.conf` — 重新連網時
- `StartInterval: 1800` — 每 30 分鐘保底
- 注意：article-crawl 需要 Chrome，catchup 不補跑（需手動）

## 核心腳本說明

| 腳本 | 功能 |
|------|------|
| `scripts/daily_briefing.py` | 完整日報 pipeline（Steps 1-8，含 HTML rebuild + API 成本報告）|
| `scripts/generate_html.py` | 從 DB 生成 docs/index.html（GitHub Pages）|
| `scripts/podcast_backfill.py` | 偵測並補精煉未完成的 podcast 集數 |
| `scripts/catchup.py` | 開機/連網補漏排程執行器 |
| `scripts/build_theme_dictionary.py` | 增量更新主題字典（每日 daily_briefing Step 5.5 執行）|
| `src/utils/db.py` | DB 連線 adapter（asyncpg 相容介面，透過 HTTPS Edge Function）|
| `src/utils/refine.py` | 精煉 pipeline：Gemini（podcast）/ Ollama→Gemini（文章）|
| `src/analysis/market_notes.py` | 跨來源共同議題分析（Gemini，UPSERT to analysis_reports）|
| `src/analysis/daily_report.py` | 每日 AI 投資簡報（Gemini）|
| `src/analysis/focus_themes.py` | 焦點主題叢集偵測（雙閘門：字典成員 + 關鍵字/量能輪動）|

## 資料庫 Schema

```
articles          - 專欄文章 + Podcast 逐字稿
  .refined_content  - Gemini/Ollama 精煉後的投資重點
  .content_tags     - ['macro','international','stock','supply_chain']（空陣列=未精煉或無投資內容）
  .embedding        - vector(768)，pgvector 向量搜尋
  .tickers          - 文章提及的股票代號陣列

analysis_reports  - 每日分析結果
  .raw_response       - Gemini 產生的完整日報
  .market_notes_json  - 跨來源共同議題 JSONB

market_snapshots  - 每日市場數據（指數、VIX、Fear&Greed）
trading_rankings  - 每日成交值前30名（US/TW）
news_items        - 每日金融新聞（預留）
watchlist         - 追蹤標的（預留）
```

## 市場數據追蹤標的
S&P500 / NASDAQ / SOX / DJI / 台股加權 / 東證TOPIX(1308.T) / 韓股KOSPI(^KS11) / VIX / 10Y殖利率 / 美元指數 / Fear&Greed

## 開發進度

- **Phase 1**: 環境建置 + DB Schema + 瀏覽器連接測試 ✅
- **Phase 2**: M1 初始爬蟲（近半年文章）✅ + Podcast 全音頻轉錄 ✅
- **Phase 2.5**: 資料精煉 Pipeline ✅（Gemini 2.5 Flash；pgvector embedding 已啟用）
- **Phase 3**: M2 市場數據監測 ✅（market_snapshots + trading_rankings 每日自動抓取）
- **Phase 4**: M3 分析引擎 ✅（daily_report + market_notes，Gemini 2.5 Flash）
- **Phase 4.5**: 焦點主題叢集 ✅（focus_themes.py 雙閘門；theme_dictionary.json 226 主題含供應鏈上下游；量能輪動路徑）
- **Phase 5**: launchd 全部已 load ✅；開機補漏 catchup ✅；Telegram 通知 ⏳
- **Phase 5.5**: DB 層 HTTPS 遷移 ✅（公司 WiFi port 443 only 環境全面可用）

## 待辦事項

- [ ] `brew install ffmpeg` — 啟用完整 Whisper 音頻轉錄（目前 fallback 至 show notes）
- [ ] Telegram Bot token — 設定後加入 daily_briefing.py 推播
- [ ] ANTHROPIC_API_KEY — 設定後可將精煉/分析全面遷移至 Claude（Haiku + Sonnet）
  - 文章精煉：`claude-haiku-4-5`（便宜，格式遵從穩定）
  - 日報/議題分析：`claude-sonnet-4-6`
  - 遷移後可解決 Gemini 免費額度每日 429 問題

## 已知限制

- Gemini 2.5 Flash 免費額度約 50-100 RPD，密集 debug session 當天可能耗盡（隔天自動恢復）
- Article crawl 需 Chrome 在 port 9222 運行，開機後需手動啟動 Chrome（`--remote-debugging-port=9222`）
- Podcast 中廣告/問答集數精煉結果為 NONE，不顯示在頁面（過濾條件：`content_tags != '{}'`）
- catchup.py 的 `from src.utils import db` 必須在 `sys.path.insert` 之後（已修正，注意維護順序）
