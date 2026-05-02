# Project: Investment Intelligence Analyst (IIA)

## 專案目標
建立一個自動化系統，整合訂閱專欄與全球金融資訊，提供具時效性的台美股投資建議。

## 技術棧
- **語言**: Python 3.12+
- **瀏覽器自動化**: Playwright（連接使用者現有已登入的 Chrome Session，via port 9222）
- **資料庫**: PostgreSQL（Supabase 雲端，跨裝置共用同一份資料）
- **排程**: 本機 Mac（launchd）
- **AI 分析**: Claude API（Sonnet 用於分析，Haiku 用於批次整理）
- **套件管理**: uv
- **通知**: Telegram Bot（LINE Notify 已於 2025/3 停止服務）

## 訂閱專欄清單（M1 文章來源）
1. https://www.macromicro.me/ — 財經M平方（總經數據為主）
2. https://vocus.cc/salon/ChivesKing — 方格子 韭菜王
3. https://statementdog.com/industry_reports — 財報狗產業報告
4. https://investanchors.com/user/vip_contents/investanchors_index — 投資錨點 VIP
5. https://www.pressplay.cc/member/learning/projects/EFB905DAF7B44F479552E5F5D955A137/articles — PressPlay 財經捕手

## Podcast 來源（M1 音頻 RSS）
- source: `podcast_gooaye` — 股癌 Gooaye（soundon.fm RSS）
- source: `podcast_macromicro` — 財經M平方 podcast（soundon.fm RSS）
- source: `podcast_chives_grad` — 韭菜畢業班（soundon.fm RSS）
- source: `podcast_stock_barrel` — 股海飯桶 WilsonRice（soundon.fm RSS）
- 每天 07:00 前執行增量抓取（`src/crawlers/podcasts.py --incremental`）
- 注意：若同題材不同 podcast 有相差觀點，兩者皆記錄，不互相覆蓋

## 核心行為規範
1. **事實準確性**：嚴禁捏造不存在的事實。若資訊無法查證，必須明確標示「查無可靠證據」。
2. **時效管理**：所有資料必須記錄 `created_at` / `updated_at`。若新文章內容與舊資訊衝突，應標記舊記錄為 `superseded`，以新資訊為準。
3. **瀏覽器操作**：透過 Chrome Remote Debugging Port（9222）連接已登入的 Session，不儲存任何帳密。
4. **不做投資建議**：分析引擎輸出的是「基於事實的推論」，最終決策由使用者自行判斷。

## 任務模組

### M1: 專欄爬蟲與同步 (Column Sync)
- 支援上述 5 個指定專欄的深度爬取（近半年文章）
- 增量更新機制（每天 08:00 / 21:00）
- 語意版本管理：識別文章中的觀點轉變（看多 → 中立 → 看空）
- 每篇文章記錄：標題、日期、作者、原文、AI 摘要、觀點標籤、相關標的

### M2: 全球金融新聞監測 (Global News Monitor)
- 涵蓋國內外主流財經媒體、法人研究報告（免費公開來源）
- 監控總經數據：CPI、非農就業、聯準會動向、PMI 等
- 監控國際股市：美股三大指數、費城半導體、VIX

### M3: 投資組合分析引擎 (Analysis Engine)
- **總經判斷**：短期（1-2 週）/ 中期（1-3 個月）/ 長期（3-12 個月）多空標籤
- **成值標的過濾**：
  - 台股：上市櫃成交值前 50 名，篩選與 M1/M2 高相關標的
  - 美股：成交值前 50 名，篩選與 M1/M2 高相關標的
- **推演輸出**：未來 1 週至 1 個月可能發動的題材及標的（標註信心度）

## 資料庫 Schema
```
articles          - 專欄文章（含觀點標籤、版本鏈）
news_items        - 每日金融新聞
market_snapshots  - 每日市場數據快照
analysis_reports  - 每次 M3 分析結果
watchlist         - 追蹤標的清單
```

## 開發階段
- **Phase 1**: 環境建置 + 資料庫 Schema + 瀏覽器連接測試 ✅
- **Phase 2**: M1 初始爬蟲（近半年文章一次性爬取）✅ + Podcast 全音頻轉錄 ✅
- **Phase 2.5**: 資料精煉 Pipeline ✅（pgvector embedding 已啟用；Haiku 篩選待 API Key）
- **Phase 3**: M2 新聞監測 ✅（market_snapshots + trading_rankings 已可抓取）
- **Phase 4**: M3 分析引擎 ✅（daily_report.py 已完成，待 ANTHROPIC_API_KEY）
- **Phase 5**: launchd plist 已產生（待手動 launchctl load）+ Telegram 通知 ⏳

## DB Schema（Migration 002 已套用）
- `articles.refined_content` — Haiku 精煉後投資相關段落（需 API Key）
- `articles.content_tags` — ['macro','international','stock','supply_chain']
- `articles.embedding` — vector(768)，paraphrase-multilingual-mpnet-base-v2
- `trading_rankings` — 每日成交值前30名（US/TW/JP）
- 444 篇文章已產生 embedding，向量搜尋已可用

## 待確認事項
- [ ] Telegram Bot token（替代 LINE Notify）
- [x] Supabase 專案已建立（DATABASE_URL 已設定於 .env）
- [ ] Anthropic API Key（Haiku 精煉內容 + M3 分析引擎使用）
  - 設定後執行 `uv run scripts/backfill_refined.py` 可補齊所有文章的精煉內容
