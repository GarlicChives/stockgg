# Project: stockgg (public daily-briefing site)

> **新 session 開頭**:讀 `~/Desktop/StockGG-ingest/SYSTEM.md` — 兩個 repo 的全景(資料流、排程、職責、踩坑)。
> SYSTEM.md 實體放在私有的 StockGG-ingest repo(因含爬蟲 / 訂閱站營運細節),
> 本檔只覆蓋 stockgg 自己的 do/don't。

## 本 repo 角色

Thin presentation layer。只渲染 HTML + 部署 Cloudflare Workers。
資料攝取、AI 分析、爬蟲全部在 companion repo `StockGG-ingest` 跑
(本機 `~/Desktop/StockGG-ingest`,私有 GitHub repo)。

## 嚴格 guardrail

| 規則 | 為什麼 |
|---|---|
| ❌ **不要引入** `SUPABASE_SERVICE_ROLE_KEY` 到本 repo | service_role 是私有 repo 專用;本 repo 用 anon |
| ❌ **不要呼叫** Gemini / OpenAI / 任何 LLM API | 公開 repo 沒任何 LLM key,也不該有 |
| ❌ **不要爬任何網站** | 法律隔離邊界,原始內容只能在私有 repo |
| ❌ **不要在這裡跑 LLM-generated 分析** | 分析是私有 repo 的事,這裡只讀已存的結果 |
| ✅ **新增或調整 query 就同步擴 allowlist** | 改 `supabase/functions/db-proxy-public/index.ts` 的 `ALLOWED`(新增條目,或修改既有 SQL 樣板 —— normalize-比對是 exact match,整段改才會通過),然後跑 `bash scripts/deploy_db_proxy_public.sh`(wrapper 內含 Supabase CLI auth liveness probe,token 失效會給繁中提示)。不擴/不 redeploy 會 CI 403 |
| ✅ **不確定查詢能不能跑時,本機跑 generate_html.py 看 403** | 是最快的 sanity check |

## 關鍵檔案

- `scripts/generate_html.py` — HTML 渲染(~4200 行;頁面結構 + 資料 payload)。2026-05 起 CSS/JS 抽成獨立檔(見下),此檔不再內嵌 ~2000 行 CSS/JS,f-string escaping 雷區大幅縮小
- `docs/style.css` — 全站 CSS(靜態原生檔,直接編輯;改檔後 generate_html.py 用內容雜湊自動 cache-bust `?v=`)
- `docs/app.js` — 全站 JS 函式(靜態原生檔,直接編輯)。個股 modal 的 `artModalData` 等資料 const 仍 inline 在 index.html(per-render 動態),app.js 以全域 scope 取用
- `src/analysis/focus_themes.py` — 題材叢集(純 Python);兩個函式:
  - `detect_industry_clusters(tw_top_volume)` — 普適 TV 累加(pan_sub 用);輸入 = `stocks_info` filter market='TW';自動 dedupe 同 focal set 為 merged cluster(`A & B & C`)
  - `detect_focus_clusters(seeds, focus_members)` — **v3**(hl_sub 用;**2026-06-08 前綴群組化**,前身 v2 2026-05-19 對齊 ingest `8f27ede`);seeds = is_focus_seed((rank≤120 OR 近漲停 chg≥9.5%) AND chg>4.45%,大跌盤日漲幅門檻翻 -3.0 見 Q16, Q16),focus_members = is_focus_member rows(Q15)。算法:種子計數 + 成題材 + 成員蒐集 + cluster 命名**全改用「· 前綴群組」**(`_sub_prefix`,取 sub「前綴·後綴」的前綴)而非完整 sub —— 字典把同題材拆成多個編輯子角度(被動元件·綜合型龍頭 / 被動元件·通路…),完整 sub 計數會把同質種子打散到各只 1 顆湊不到門檻(2026-06-08 國巨−2.34%/8043+9.72% 都成種子卻因散在不同完整 sub 而無被動元件題材的根因)。前綴計數後同前綴種子數 ≥ `FOCUS_MIN_SEEDS`(2) 即熱門題材;成員 = 字典內任一 sub 前綴 match 者 today 有交易,chg > `FOCUS_SENTINEL_THRESHOLD`(-3) 入 `focal`、< 入 `sentinel`。同股可同時出現在多前綴題材(國巨在 被動元件/MLCC/電容器),靠 `_merge_identical_focal` 只併 focal 完全相同者。`FOCUS_PREFIX_BLOCKLIST`(目前 `{"黃仁勳"}`)= 人名 / 非產業前綴不成公開站題材標題(成分股仍出現在其他產業前綴題材,零覆蓋損失;產業地圖 Q38 的「黃仁勳」node 是另一 dataset 不受此黑名單影響)。**Q25 歷史趨勢自動跟著前綴重算**(`hot_subs_from_seeds` 同步改前綴 + 同黑名單)。`generate_html` topic ⓘ 關聯亦把 `topics_by_focus_theme` 收斂到前綴 key(`_focus_sub_prefix`)才對得上。**v1 廢**(2026-05-18 `bd85f1d` → 次日 `8f27ede` 撤,hot_seed / limit_hot_seed / volume_universe 機制完全移除)
- `src/utils/db.py` — async DB client(用 `SUPABASE_ANON_KEY` + `db-proxy-public`)。**2026-05-27** `_call` 內建 5xx retry(500/502/503/504/522/524/546/548 + 連線層 timeout/network/protocol error → 0.5s/1.5s/3s backoff 共 3 次 retry,total ≤ 5s)。從此 caller 端不必再對 transient Edge 5xx 自己包 try/except,只要該 query 真的壞才會 raise 上來
- `data/theme_dictionary.json` — statementdog 主產業 / 子產業階層字典(2026-05 改 schema:ticker-centric `stocks` 物件,純台股;由 ingest 端 `scrape_statementdog_industries.py` 產生再 sync 到本 repo)。**main='近一年焦點'** 是 ingest 端人工編彙的長線觀察題材(62 sub / 230 ticker;sub 名稱「前綴·後綴」可用 「·」 split 群組),公開站「熱門題材」頁有獨立 sub-tab「🌟 焦點」,跟「📊 泛分類」(原 statementdog 47 main) 並陳
- `supabase/functions/db-proxy-public/index.ts` — Edge Function 含 SQL allowlist(目前 **36 條**):
  - Q1-Q8 日報基本資料、**Q9 v2** catalyst_events ±14/21d + `visible = TRUE` filter(ingest `4d5e7cc` 起;遠期 events visible=false 不出,daily cron 隨日期 flip true)、Q10 market_notes
  - **Q3 / Q4** rank_date 必帶 `AND rank IS NOT NULL` — `trading_rankings` 內除真實排名列(rank 1..N)還有 rank=NULL 雜列(special / focus_member / market_notes_ref),後者 rank_date 可能領先真實排名日。盲取 `MAX(rank_date)` 會選到幽靈日期 → 公開站整頁空。加 filter 確保永遠回退「已完整抓到 top-N 的最新交易日」(公開站鐵則:永遠不空、永遠呈現最新完整交易日)
  - Q11 theme_history 180→**400 days** retention
  - Q12 stock_meta(公司基本面快照;2026-05-20 起含三率 gross/operating/net_margin + *_yoy_dir + 營收 revenue_mom/yoy,選股雷達頁(原「焦點股」)5 欄用,ingest `57c7e8b`;`8b155c1` 再加成長股欄 revenue_yoy_3m_all_positive + gross_profit/operating_income/pretax_income/net_income_yoy;**2026-05-23** 加 peg_ratio / peg_status / eps_ttm_yoy(ingest `c24faee`)—— PEG 顯示於熱門題材 cluster 卡(平均 PEG metric badge + 外層 / chart modal 排序 chip)與選股雷達 PEG 欄(status-aware:ok_ttm/ok_q 顯數字 + TTM/季 小標、eps_declining 顯「EPS 衰退」、low_growth 顯「低成長」、insufficient_history 顯「—」;配色 <1 綠 / 1-1.5 灰 / >1.5 紅,`peg-low/-mid/-high`)
  - Q13 ticker_close_history 讀取(讓近一年焦點 cluster chart modal 能畫加權指數,因 theme_history 沒此 main 的 row);2026-05-22 起 SELECT 含 `high`(每日盤中最高價)。Q6/Q14/Q15(trading_rankings 今日列)同步加 `high` —— 供「選股雷達 > 新高股」:新高定義 2026-05-22 從「收盤創新高」改為「今日盤中最高價 ≥ 過去 252 日最高盤中價」(收盤定義會把盤中未觸及真實 52 週高、僅收盤超過自家近期收盤上限的股票誤判入選,如 3030;ingest `5a530ea` 持久化 high)。**2026-05-25** 加 `open` / `low`(ingest `4ea7c3e`):Q6/Q14/Q15 加 `open`/`low`;Q13 加 `open`/`low` 並把 retention 400→**730 天**(2 年),供 P2 個股 modal 日 K 線。**早盤漲停股 sub-tab + chip + filter 已於 2026-05-25 移除**(原版用 `chg≥9.95 AND |O−C|<0.01 AND |H−C|<0.01 AND |L−C|<0.01` 判 open=high=low=close 整日鎖死,但 user 想升級為「0930 前漲停 + 0930 後無 tick 跌破」需 5 分 K intraday tick 資料,現有 ingest 無此來源 → 整功能撤掉)
  - Q14 special rows(處置 / 漲跌停 not in top-50)WHERE `extra->>'is_special'='true'`
  - **Q15 v2** focus_member rows(ticker 屬「近一年焦點」字典任一 sub 且 today 有交易,ingest `8f27ede` 起;v1 是 `is_volume_universe`,次日撤)WHERE `extra->>'is_focus_member'='true'`
  - **Q16** focus_seed ticker list((rank ≤ 120 OR 近漲停 chg ≥ 9.5%) AND chg > 4.45%, ingest `8f27ede`;`a23e1cc` 加近漲停豁免 —— 漲停股成交值鎖死壓抑會讓 rank 失真掉出榜外;`c1490b8` 排名門檻 300→120 + 漲幅 4.5→4.45,排名門檻獨立成 ingest config `FOCUS_SEED_MAX_RANK`、與 universe 寫入筆數 `RANKINGS_UNIVERSE_N`=300 解耦)WHERE `extra->>'is_focus_seed'='true'`。**2026-06-08 大跌盤抗跌模式**(ingest `c587cd1`):當日全市場 breadth = 上市櫃 4 位數普通股(`^\d{4}$`,排除權證 / ETN / 反向 ETF —— 無漲跌幅限制會嚴重失真)chg>0 家數 / 有報價總家數,< `FOCUS_CRASH_BREADTH_THRESHOLD`(0.20)即 crash 日,種子漲幅門檻 4.45 → `FOCUS_SEED_CRASH_MIN_GAIN_PCT`(-3.0,收逆勢漲或跌幅<3% 的相對抗跌股),rank≤120 流動性閘 + 近漲停豁免不變;非 crash 日完全同現行。breadth + crash 寫進**當日 `^TWII` `market_snapshots.extra`**(`tw_breadth_up` / `tw_breadth_total` / `tw_breadth_ratio` / `tw_crash_mode`,jsonb object-merge upsert 與 `extra.name` 共存),stockgg 經 Q2 同條 query 取得(無新 allowlist),crash 日於「焦點」sub-tab 頂渲 `.crash-banner`(`build_focus_html` 的 `tw_breadth` 參數;欄位缺則不渲)。種子規則本體 detect_focus_clusters / hot_subs_from_seeds 零改動(只消費種子旗標,crash 門檻由 ingest 翻)。**精選閘(2026-06-08,純 stockgg)**:crash 日題材仍偏多(35+),`generate_html` 在 detect_focus_clusters 後套 `_crash_distill_clusters(focus_hl_clusters, stocks_info)` 濃縮成 ≤`CRASH_DISTILL_MAX`(8)個「真抗跌題材」—— 兩道硬閘(全成員 focal+sentinel):抗跌率 focal/(focal+sentinel) ≥ `CRASH_DISTILL_MIN_RESILIENCE`(0.5,排除靠 1-2 龍頭撐其餘崩的假抗跌)+ 成交值加權平均漲跌 ≥ **動態門檻**;過閘按加權漲跌 desc 取前 8(顯示順序仍交給外層 TV sort chip)。**動態門檻 = TWII 當日漲跌(snaps `^TWII` chg)+ `CRASH_DISTILL_BEAT_MARKET`(1.0,贏大盤至少幾 pp)**(2026-06-08 改,取代固定 `MIN_WCHG`):溫和崩盤(TWII -1.5 → gate -0.5)自動收緊、重挫日(TWII -5 → gate -4.0)自動放鬆,絕對地板交給抗跌率≥0.5 那道閘把關(過半成分守 -3% 上 → 不會是 -5% 災難題材);TWII chg 取不到時退 `CRASH_DISTILL_FALLBACK_WCHG`(-2.5)。**只看價格抗跌,不加法人淨買閘**(user 定 2026-06-08:法人淨流在大跌盤被權值股如 2330 機械性主導 + 同股跨多題材重複計入而失真)。**只在 crash 日(tw_breadth 非空)啟用**,平盤/多頭日顯示全部題材。banner 末句動態顯「精選為 N 個真抗跌題材」。2026-06-08(TWII -3.48 → gate -2.48)驗證 35→5(生技醫療/被動元件/液冷散熱/散熱模組/玻纖布;資料中心 -2.49 差 0.01 出局)
  - **Q17** ticker_net_inst_history 攤平歷史 net_inst (NTD = T86/3insti × close, ingest `ed3b2e9` 起;取代從 `theme_history.focal_breakdown` 反向索引建 ticker_net_inst 的舊 path — 對「純近一年焦點 ticker」focal_breakdown 永遠缺、反向索引拿不到)
  - **Q13 / Q17 分批 fetch + Q13 fail-fast**(2026-05-27):`_hist_tickers` 約 470 檔,單一 `WHERE ticker = ANY($1::text[]) ... 400 days` 在 CI 偶發 `546 <none>`(Supabase Edge isolate CPU / 6MB response 上限) → kline.json 沒寫 → 下次 wrangler deploy 用「沒 kline.json」的 docs/ 全替換 CF Workers Static Assets manifest,把上次好的 kline.json 也抹掉 → 用戶端 404 直到下個 cron。`generate_html.py` 內 `_fetch_ticker_batched` 把 Q13 / Q17 拆 60 ticker 一 batch + per-batch 2 次 retry(linear backoff)。Q13 全 retry 仍失敗 → `raise SystemExit` 中止 workflow,讓上次成功的 deploy 留在線上;Q17 失敗只 print 警示(degraded UX 不影響 kline)
  - **Q18 / Q19 / Q20** 主動式 ETF(ingest `f5faa21` → `edc8d49` v2):Q18 `active_etf_meta` master 按 AUM desc;Q19/Q20 v2 加 has_baseline CTE — DB 內該 ETF 只 1 day holdings 時 lots_chg/action = NULL,UI 顯警示「無前日 baseline」+ chip 不渲(避免硬標 new 失真);Q20 多 pct_of_float(lots × 1000 / stock_meta.shares_outstanding × 100)。**2026-05-27** Q19 SELECT 加 `(SELECT d FROM latest) AS data_date`(latest CTE 的 max holding_date 攤平到每 row),供主動式 ETF 頁每 tab 頂部 bar 與個股 modal「持股主動式 ETF」section 顯示「持股更新 YYYY-MM-DD」。db-proxy(`npm:postgres`)把 DATE 序列化成 `2026-05-27T00:00:00.000Z`,Python 端 `_aetf_date_fmt` 切前 10 碼回標準 YYYY-MM-DD。**2026-06-04 公開頁只列「純台股 AUM 前 10 大」**:generate_html `_AETF_EXCLUDE={00990A,00400A,00997A}`(全球/美國/無持股,user 指定)+ 需有持股 + 依 AUM 取前 10(`active_etf_meta` 仍回全部,Python 端 filter)。`market` 欄位不可靠(00997A 美國增長持股卻全標 TW)故用碼名單而非自動判定。ingest 端對應只爬保留檔(見跨 repo prompt)。**2026-06-04 ETF 頁改版**:版面 =「每日加減碼趨勢」圖(標題行右側內嵌「資料已更新 n/total」badge)→ 橫排 sub-tab(取代下拉)+ 各檔明細(資訊 bar / 今日異動 / 全持股)。**「資料已更新 n/total」改 client-side 即時**(`docs/app.js` `aetfUpdateBadge` + `IIA_TW_HOLIDAYS`):每交易日 13:30 收盤後歸零(目標日=今日,尚無資料→0,隨各家公布回補)、**週末/休市日不歸零**(目標日=最新資料日);全到綠、未到琥珀。server 嵌 `window.IIA_AETF_UPDATE={dates,total,latest}` payload + 初始值,JS 每 60s 依台北時間覆寫。`IIA_TW_HOLIDAYS`=內嵌台股休市日 Set,**每年需更新一次**(TWSE 官方為準)。**跨 ETF 共識動向(共識股淨流 / 清倉 / 題材)整組已於 2026-06-05 移除**(user 不要,`_build_aetf_consensus` 已刪);頁面只剩趨勢圖 + 各檔明細。趨勢圖 `_build_aetf_trend`(server-render CSS 長條,上紅加碼/下綠減碼):新 allowlist query 撈近 35 天多日持股,stockgg diff 同 (etf,ticker) 連續持股日 lots 差 × 每張價 → 逐日加/減碼金額。**retention 目前 14 天故約 10 交易日,需 ingest 延長到 35 天才滿一個月**(全清倉整筆消失不在連續列差內 → flow 略低估)。sub-tab 沿用既有 `showAetfTab`(本就支援 `.aetf-tab-btn[data-aetf]`)
  - **Q21** 大盤 / 櫃買指數歷史(ingest `11a88d4` → `76f6728`):`market_snapshots` 撈 `^TWII` / `^TWOII` 過去 **1095 天** OHLCV(2026-05-29 retention 400→1095 配合 V3.2 backtest 3y 跨多空 regime 樣本),供:(1) cluster chart modal 大盤 / 櫃買 overlay 線(用 close)(2)「📈 趨勢」menu 大盤 K + MA10/60/200 + 副圖 「大盤距 MA60 偏離 %」。2026-05 起 render-time yfinance 全移回 ingest,generate_html.py 不再 `import yfinance`(指數歷史走 Q21、MA20 乖離走 Q13 自算、market_notes ticker 走 Q8)
  - **Q26 / Q27 / Q28 / Q30**(2026-05-29 對應 ingest `024e4d2` 完成 focus_radar_history 3y backfill):
    - **Q26** `focus_radar_history` 半年聚合(intersect_count / breakdown / universe_size),retention 1095d → 給「📈 趨勢」副圖 nh_count / chip_count 線、計算 risk composite
    - **Q27** `focus_radar_history` 最新 row(intersect_tickers / per_ticker_conds / pot_subtype),給選股雷達 sub-tab status block + 未來 Phase 3 拔掉 build_focus_stock_page 5 條件 inline 計算的 source
    - **Q28** focus_radar_history `per_ticker_conds` 日期區間(date range params),給 stockgg backtest script 分批拉
    - **Q30** ticker_close_history 日期區間版(date range params),3y 範圍給 backtest 用
  - **Q22 / Q23**(2026-05-22)供「選股雷達 > 籌碼股」sub-tab + 交集股的「籌碼」條件:Q22 `ticker_chip_history` 近 30 天 daily 三大法人分項 net_shares(算近 3 日外資/投信佔量%);Q23 `ticker_holder_dist` 近 60 天 TDCC 集保週資料 `levels`(17 級持股分佈 JSON,每級 h/p/s)。stockgg 端改金額定義(免 TDCC 固定股數級距對高/低價股失真,如 ¥3000 股 1 張即 300萬):散戶 = 級距上限 × 股價 < 1000萬、大戶 = 級距下限 × 股價 ≥ 5000萬;兩週 diff(同一最新收盤價,週變不受股價波動干擾)= 散戶 / 大戶持股比週變;籌碼鎖定率 = 大戶持股比週變。三區邏輯:散戶持股比週減(必須)+【投信買超≥5%量 / 外資買超≥10%量 / 大戶持股比週增≥1.5】≥1 + 排除【外資賣超≥10%量 / 投信賣超≥5%量 / 大戶持股比週減】。散戶 / 大戶「週減」用 0.3pp 緩衝濾 TDCC bucketing 噪音(`_HOLDER_NOISE`);原第3區「散戶買超」排除已移除 —— 第1區強制散戶週減,該排除恆 false(死條件)。兩表 ingest 端 `chip_history.py` / `holder_dist.py` 寫入,universe = FOCUS_MAIN。籌碼股版型同其他 sub-tab(無自訂籌碼欄);「籌碼」也是交集股的符合條件之一。**主力 / 前十大券商條件做不到** —— 逐日券商分點是 TWSE 付費資料,無免費來源
  - **Q25**(2026-05-28,取代 2026-05-27 廢的 Q24)供「熱門題材」hl_sub cluster header 兩 chip(連續上榜天數 / 近 20 日上榜率)+「📈 趨勢」menu 上圖兩條序列(熱門題材數量 / 題材延續性):`SELECT rank_date, ticker FROM trading_rankings WHERE market='TW' AND extra->>'is_focus_seed'='true' AND rank_date >= current_date - 180 days`。stockgg 端拿到 raw seeds per date,用 `focus_themes.hot_subs_from_seeds`(detect_focus_clusters step 1-2)逐日重算 daily hot_subs。Q24 廢的原因:讀的是 ingest 寫的 theme_history sub_industry,但 ingest 寫條件「字典成員 ∩ universe ≥ 2」≠ stockgg「is_focus_seed ≥ 2」,數量差很多(84 vs 真實 8)。新架構好處:未來 detect_focus_clusters 邏輯異動,改 stockgg 程式重 regen 就重算所有歷史,不必 ingest 端 backfill
  - **Q31-Q35**(2026-06-03)各頁「資料最後更新時間」badge:回各資料源表最新寫入 timestamptz,stockgg 端 `_fmt_data_stamp` 轉台北時間(UTC+8)顯示 `YYYY/MM/DD HH:MM:SS`。Q31 `trading_rankings` MAX(created_at) market='tw'(熱門題材 / 選股雷達非籌碼,~17:30)、Q32 `ticker_chip_history` MAX(updated_at)(選股雷達「籌碼資料更新」,21:10;2026-06-04 起與 Q31「資料更新」併同一行右上 `_stamp_badge_multi`,不另佔列、也不再 inline 在 sub-tab pane)、**Q31/Q32 帶日期(+ chip 帶 ticker array)filter 走索引**(2026-06-03 踩坑:純 MAX 全表掃描大表在 cron 寫入尖峰撞 Edge isolate CPU 上限 5xx → badge 缺漏。Q31 走 `(rank_date DESC, market)` 索引 range、Q32 走 PK `(ticker, rank_date)` 索引)、Q34 `analysis_reports` MAX(created_at)(市場話題 / 國際金融)、Q35 `market_snapshots` MAX(created_at)(趨勢)。badge 由 `_stamp_badge` 渲染進各 tab pane 頂部右上(`.data-stamp` CSS);單條 query 失敗(403 / 空表)只是該頁不顯 badge,不中斷 render。**主動式 ETF 頁無頁面層級 badge**(2026-06-04 改):各家發行商官方公布時間不同,改用 `SELECT etf_code, MAX(updated_at) GROUP BY etf_code` 取每檔自己的更新時間,顯示在各 ETF tab 頂部「持股更新 YYYY/MM/DD HH:MM:SS」(取代原 data_date 日期);原 Q33 單一 MAX(會誤導成全頁同時更新)已移除
  - **Q38** 產業地圖(🗺️ 產業地圖 tab,2026-06-07;ingest 週日 09:30 cron 爬 statementdog 焦點產業階層寫 `industry_focus_map`):`SELECT focus_tag, focus_name, axis, axis_kind, axis_order, sub_industry, sub_order, description, ticker, stock_name, market, rating, rating_rank FROM industry_focus_map ORDER BY focus_name, axis_order, sub_order, rating_rank DESC, ticker`。~1095 列編輯型慢變,整表一次撈。**呈現 = 供應鏈蜘蛛網有向圖(2026-06-07 改版;原 flat 清單廢、原「交集股連線」也廢)**:`build_industry_map_page(rows, stocks_info, supply_edges)` —— 節點=焦點產業(2026-06-08 起 generate_html 端 `_INDMAP_FOCUS_EXCLUDE={"黃仁勳"}` 濾掉人名 / 非產業 focus 的 rows → 該 node 不生成、連到它的 Q39 邊按名稱一併濾;原 50 → 49),**邊=焦點間供應鏈有向邊(Q39,from=上游→to=下游,箭頭指下游)**,非交集股。**節點發亮 = 該焦點今日成分股的成交值加權平均漲跌幅**(用 ticker join 記憶體 `stocks_info`,紅=今日強/綠=弱/灰空心=今日無成交資料;加權漲幅 ≥ `HOT_THRESHOLD=2.0` 且 cov≥0.2 → 脈動光暈)。`app.js`:`_initIndmapGraph`(手刻 Fruchterman-Reingold 力導向 + radius 解重疊 + 原生 SVG glow + **有向邊箭頭/relation hover tooltip**;**滿版 = viewBox 寬度跟容器寬高比走填滿、無滾輪縮放/拖曳**)、`_imHeatColor`、`_imLayout`。`.im-page` 用 `.wrap` 90% 寬(無 1080 cap)。payload `window.IIA_INDMAP_GRAPH={nodes,edges:[from,to,strength,relation],hot}`。**同產業上下游**(focus 內 上中下游)→ 點節點 `imOpenFocus(i)` 塞放大版 `#im-modal`:**上方 = 題材趨勢圖**(`_imRenderSubChart` 重用 `_computeClusterSeries`/`_rebaseSeries`/`_computeIndexSeries`:左欄成分股清單(今日報價,點擊 `imToggleTicker` 從加權指數排除/納入,本地 `_imTickerDis`;`.im-mc-side` `overflow-x:hidden`+`scrollbar-width:none` 無捲軸)+ 焦點股加權指數 + 大盤/櫃買 rebase + 三大法人淨流 histogram + 1M/3M/6M/1Y/ALL chips + **比照題材 modal 的 指數/個股(`imSetChartMode`,strength=各檔各一條)+ 當日/累計(`imSetNetMode`)切換**,本地 `_imPeriod`/`_imChartMode`/`_imNetMode`);**下方 = `#im-detail-store` 內 `#imf-{i}` 階層**(axis 上中下游/受惠層 + 個股 + 星級,描述+星級保留)。**點子產業標題 `imPickSub(i,subIdx)` → 上方圖切到該子產業**(`window.IIA_INDMAP_SUBS[i]={name,all,subs:[{name,tickers:[{t,n,chg,close,tv}]}]}`,ticker 帶今日報價供左欄)。為此 **industry-map 純台股 ticker 已併入 `_hist_tickers`** → history.json 的 ticker_close/ticker_net_inst 覆蓋(255/304 有資料,缺的 49 檔 ingest `ticker_close_history` 無 row,該子產業圖顯「暫無足夠歷史」)。modal 滾輪只作用 modal 內、無捲軸(scrollbar 隱藏);**`.im-modal-body` 必須給 `max-height:calc(84vh-58px)` 才捲得動 —— flex:1 在 dialog `max-height`(非固定 height)下不 cap 子元素,body 會長到滿內容、dialog `overflow:hidden` 只是切掉**;`_imBindModalScroll` wheel guard **手動 `body.scrollTop+=deltaY`**(lightweight-charts canvas 會吃掉 wheel 讓 body 捲不動)+ 擋頁面穿透;關閉 dispose 圖。modal 內個股 → `imShowCross()`(`window.IIA_INDMAP_CROSS`)。graph hover tooltip `placeTip` 夾邊+翻轉防被 `overflow:hidden` 切。**頁面層級「橫跨多焦點明星股」chip 已移除**。純讀,不重算
  - **Q39** 焦點產業供應鏈有向邊(`industry_supply_edges`;ingest `4162523` Gemini 從 Q38 階層 + 描述語意推導,from=上游供應端 / to=下游需求端 / strength 1..3 / relation 一句話依據):`SELECT from_focus_tag, from_focus_name, to_focus_tag, to_focus_name, relation, strength FROM industry_supply_edges ORDER BY strength DESC, from_focus_name`。~129 條週更慢變,整表撈,給產業地圖蜘蛛網的「線」。ingest 端 `src/analysis/industry_supply_edges.py`(`--once` 重跑;邊只能來自既有階層語意、不得無中生有)
  - **allowlist 字串 normalize 規則**(2026-05-28 踩坑):`ALLOWED` 是 `Set<string>` 存 raw 字串,check 時用 `normalize(query)` 去 Set 找。所以 allowlist 條目必須**已是 normalize 過的形式**(全小寫 + whitespace 單空)。例如即使資料是 `market='TW'`,allowlist 也要寫 `market='tw'`;code 端仍寫 `'TW'`,normalize 比對時統一變小寫,真正 execute 時走 raw query 'TW' 才能對到資料
- **新 top nav tab「📈 趨勢」**(2026-05-29 V3.2 重構,原 2026-05-27 兩 chart 版本廢):
  - 5 個 chart pane:大盤 ^TWII K(+ MA10/60/200 + risk chip)→ 櫃買 ^TWOII K → 副圖 nh_count line (+12 警示) → 副圖 chip_count line (+1σ trigger) → 副圖 大盤距 MA60 偏離 % (+8% 危險區)
  - risk chip(主圖右上)= `z(TWII_60d_ROC, 20d) + z(nh_count, 20d)`(V3.2 backtest in-sample AUC 0.949 for BEAR_60d_-15%):≥+1.5 🔥 危險、0~+1.5 ⚠ 警戒、<0 ☀ 安全
  - 廢掉舊版「熱門題材數量 + 題材延續性」上圖(V3.2 backtest 證明這兩個 ρ < 0.2 是 noise)
  - `docs/app.js` `_initTrendCharts` / `_renderTrendCharts` 對應重寫:5 chart 全 timeScale + crosshair sync,MA10/60/200 + volume overlay 都用 lightweight-charts 內建 series 疊
  - payload `window.IIA_TREND` = `{index:{TWII,TPEX},radar:[...半年聚合],risk_today:{composite,level,label,...}}`,server-side render 一次算好
- `.github/workflows/market_briefing.yml` — render + deploy(07:30 / 18:15 / 23:15 TW cron + repository_dispatch)。**push 不會觸發**,hot-fix 後要 `gh workflow run "Publish daily site"` 手動跑。`concurrency: publish-daily-site` 同 workflow 排隊不互相取消;commit-and-push step 含 `-X ours` rebase retry x3,避免本地 dev push 與 bot 撞 race。必須 `git add docs/index.html docs/history.json` 兩檔(漏 history.json 會在 rebase retry path 卡 unstaged changes,2026-05-19 踩過);wrangler-action wranglerVersion 必 pin 具體版本(`"4"` 浮動會撈到 4.86.0 撞 npx prompt 卡住,2026-05-20 踩過)。三個 action 升 node24-capable 版本(2026-05-22 從 v4/v4/v3 升,舊版跑 Node 20,GitHub 2026-06-02 起強制 Node 24):`actions/checkout@v6`、`cloudflare/wrangler-action@v4` 用浮動 major tag;`astral-sh/setup-uv@v8.1.0` 必 pin 確切版本(setup-uv 自 v8 起移除浮動 major tag,`@v8` 不存在,踩過 → CI「Set up job」失敗)。**verbose 用 `WRANGLER_LOG: debug` 環境變數**,wrangler v4 deploy 子命令不認 `--log-level` flag(yargs validation error 會讓 deploy abort,但 wrangler-action 把錯誤吞掉,workflow 假 success,連續多次 deploy 等於沒跑,2026-05-25 踩過)
- `docs/index.html` — 渲染輸出(generate_html.py 寫入,bot CI push)
- `docs/history.json` — chart modal 用的歷史 payload,~5MB,含:
  - `history`: theme_history rows({"main||sub": [{d, s:{ticker:[tv,chg,close,net_inst,shares,**volume**]}}, ...]})(6-tuple,volume 是 2026-05-18 起加的)
  - `index`: TWII + TPEX 指數
  - `ticker_close`: per-ticker 400 天 close+shares(Q13)
  - `ticker_net_inst`: per-ticker daily net_inst(跨 main 反向索引,給近一年焦點 cluster 用)
- `docs/kline.json` — 個股 modal 日 K 線 lazy fetch payload(P2 2026-05-25 v3)。**單一檔案含全部 ticker**:`{"b": build_stamp_iso, "k": {"<ticker>": [[d,o,h,l,c,v],...], ...}}`。~8MB / gzip ~2MB,450 ticker。`.gitignore` 排除(避 8MB 進 repo),wrangler v4 不 respect .gitignore 仍會上傳。`docs/app.js` 的 `_loadKlineAll` 第一次任意 ticker 點 modal 才 lazy fetch — **必須用 `fetch('kline.json', { cache: 'no-cache' })`,不可加 `?_=Date.now()` 隨機 query**(每次 URL 不同會讓 Cloudflare 邊緣節點每次 cache miss → 撞 Workers Static Assets manifest sync 延遲導致 404)。**指數退避 retry**(0/2/5/10/20/30 秒共 6 輪)讓 propagation 延遲場景也能拿到資料;全失敗才拋錯(modal 顯「載入失敗」而非「本檔尚無」誤訊息)。解析後 in-memory dict 共享,後續 `_fetchKline(tk)` 從 dict 拿。**v1 per-ticker `docs/kline/<tk>.json` + v2 雙寫 fallback 都廢棄**:v1 450 個 manifest entry edge sync 延遲嚴重;v2 `docs/kline.json` 與 `docs/kline/` 目錄 path collision 疑似讓 Cloudflare manifest silent drop `/kline.json` entry,造成 10 分鐘後仍 404(假設 A 已驗證為主因)。v3 只寫 `docs/kline.json`,generate_html.py 內主動 `shutil.rmtree(OUT_FILE.parent / "kline")` 清掉舊目錄避殘留 collision。相當於用「per-regen 寫靜態檔」取代「client-side db-proxy fetch + 暴露 anon key」。Q13 維持 400 天(730 天會爆 db-proxy 6MB response 上限),所以 kline 也只有 ~400 天,UI 時間粒度 chip 上限 1Y。**JSON 結構含 `b: build_stamp`** 讓每次 regen 內容 hash 必變,wrangler 強制重傳。(2026-05-25 連環踩過 .gitignore / .assetsignore / wrangler manifest cache / per-ticker entry sync / cache-bust query 反害 / path collision 六層誤判)。**2026-06-08 今日根錨定(處置股錯價根除)**:kline 的「今日」那根 K 一律以 `trading_rankings`(stocks_info)的權威 OHLC 覆寫/補上,**不再純信 Q13**。根因 = 同一檔「今日收盤」有兩個來源(卡片 pill/cluster 走 trading_rankings Q14、modal K 線走 Q13 另一支 ingest job),Q13 落後 / 不覆蓋該檔 / kline.json 邊緣 stale 時,處置/跌停股(日跳 ±10%)的 modal 末根顯舊價、與 pill 今日價打架(反覆發生、間歇性,只在大跳股顯眼)。修法:組 kline 時 Q13 已有今日且收盤一致 → no-op;Q13 今日收盤與權威不符 → 覆寫;Q13 落後沒今日 / 完全沒這檔 → 補一根權威今日 bar(O/H/L 缺退回 close 避免蠟燭畫不出、volume 用成交值/收盤近似)。保證 modal 末根 ≡ pill close,與 Q13 是否落後無關。print「今日根錨定 trading_rankings:N 檔補/正」
- `wrangler.jsonc` — `assets.directory: docs` → Workers 整個 docs/ 當靜態 asset 服務

## 前端架構速覽

- **單頁 SPA + main tab + sub tab**:
  - main tab(top nav):熱門題材(首頁)/ 選股雷達(原「焦點股」)/ 主動式 ETF / 🗺️ 產業地圖 / 市場話題 / 國際金融 / 🛡️ 風控
  - 熱門題材內 sub-tab:🌟 焦點(`hl_sub` level,展示 main='近一年焦點' cluster + 前哨 section)/ 📊 泛分類(`pan_sub` level,原 statementdog 47 main)
  - 兩 sub-tab 共用 cluster card 排行版型,各自獨立 sort state(`_clusterSort[level]`)
- **inline payload**(HTML script tag 內):
  - `IIA_CLUSTERS.hl_sub` / `IIA_CLUSTERS.pan_sub`(各 sub-tab 的 cluster + focal ticker)
  - `artModalData`(各 ticker 的「持股主動式 ETF」表 HTML 片段,個股 modal body 用;2026-05-20 取代舊的 analyst consensus + 公司介紹)
- **lazy fetch**:`history.json`(modal chart 開啟才 fetch,no-cache 強制 revalidate),`unpkg lightweight-charts`(同上)
- **互動點**:
  - 廣泛概念股 chip 濾除(universal toggle)→ FLIP 動畫重排 cluster(threshold:cluster 數 >20 用 >3,否則 >1)
  - 外層 sort chip(成交金額/平均漲跌/平均乖離/平均 PE/平均殖利率/平均 β)→ per sub-tab state,重複點切 desc/asc
  - 內層 cluster header badge(漲跌/乖離/PE/殖利/β)→ per-cluster focal pill 排序,setFocalSort(cardId, key);預設 chg desc
  - chart 時間粒度 chip(1M/3M/6M/1Y/ALL)→ 過濾 series 後 rebase to 100;1Y 維度需要 ticker_close_history 400 天 backfill 完整
  - chart modal:左欄 ticker 列表 (vertical, by tv desc, 可 disable),右欄兩 chart 對齊(共用 priceScale minimumWidth) + 雙向 crosshair sync + 開啟動畫 + 三大法人 daily/cumulative 切換
  - chart modal 排序長條(`.tc-topbar`):排序 chip 左緣靠 `padding-left:56px`(= `.tc-nav` 寬)切齊中段 `.tc-panel`;chip 右側 `#tc-counter` 顯題材編號 `N/total`(N = `_tcSortedClusters` 位次,同左右導覽順序;`_tcUpdateCounter` 在 `_renderThemeChart` 內更新);關閉 X 在長條右端(`.tc-close` `margin-left:auto`,非 `.tc-hdr`)
  - 個股 modal(持股主動式 ETF 表)、CSV 下載、site search、share button
- **看高做低股**(選股雷達 sub-tab,2026-06-04 新增;`_is_bowl_breakout`)= 碗型底帶量突破:近 120 日收盤二次擬合開口向上(a>0)+ 底部落中段 30~70% + R²≥0.35(平滑像碗)+ 碗深(左緣-谷底)/左緣 ≥0.13 + 今日收盤由左緣頸線下翻上 + 今日成交金額 > 視窗(不含今日)均成交金額 ×3.0。資料源 = ticker_close_full(Q13)+ 今日 stocks_info,**無新 DB query**。參數來自 1336 組密格交叉、近一年事件驅動回測(進場次日開盤、停損頸線、停利月線)的全場最佳(Sharpe 0.39、大賺小賠 R≈11)。**碗型底突破是單日事件 → 多數交易日該 sub-tab 為空**。W底因回測太弱(PF~2)不納入。量 gate 用成交金額(close×volume)非股數,對齊出量股定義
- **2026-06-04 回測調參**(既有條件):出量股門檻 ×3→**×5**(前5日均成交金額);新高股 252日→**150日**;潛力B 糾結 2.5%→**3.5%**、量 ×2→**×1.5**。皆為近一年回測最佳(註:這些是「篩選門檻」;回測另含 +8% 追價進場 / 紅K低停損,屬 user 個人下單交易管理,**不在篩選器內**)
- **潛力股**(選股雷達 sub-tab,2026-05-23 更新)三條件 OR(均過全站季線 gate):
  - A(多頭排列):MA5 > MA10 > MA20 且 close < MA20 × 1.15
  - B(糾結突破):MA5/10/20 三線糾結((max-min)/mean < **3.5%**) + close > all MAs + close ≤ MA20 × 1.05 + 近 5 日均成交金額 > 近 30 日均 × **1.5**
  - C(回踩股,sentinel-only):前一交易日入選交集股 + 今日跌 > 3.5% + 仍高於月線(close > MA20) + 今日成交金額 < 前一交易日 × 0.25
  - `_was_intersect_stock`(計算「前一交易日入選交集股」名單)同步用新 A or B(不含 C,C 恆 sentinel)且不含 chip → 是 actual intersect 的下界
- **chip 系統**(2026-05-18 ingest 5a172be 起):
  - `.sp-tag.tag-strict` 嚴處 紅底(`punish_type='strict'`)
  - `.sp-tag.tag-punish` 處 橘底(`punish_type='normal'`)
  - `.sp-tag.tag-limit-up` 漲 紅底
  - `.sp-tag.tag-limit-down` 跌 綠底
  - 共用 `_flag_chips(info)` helper,_stk_pill + rank_rows_html 都用
- **rank=NULL handling**:special row(rank=NULL,extra.is_special=true)在 ranking table 顯「—」+ chip
- **前哨 section**(hl_sub cluster 才有,2026-05-19 v2 規格):由 `detect_focus_clusters` 提供 `cluster.sentinel`(題材成員 today 交易者依 `FOCUS_SENTINEL_THRESHOLD` (-3%) 切;chg > -3 入 focal、< 入 sentinel)。chip 用 `_stk_pill` 顯漲跌%(跟 focal pill 同樣式,加 `data-sentinel="1"` 區隔)。inline toggle button 在 focal pills 末段,點開後 panel max-height + opacity 動畫展開(`.cluster-sentinel-stocks[hidden]` 配 `toggleSentinelInline()`)。**舊版**(theme_dictionary 內該 sub 的完整 ticker list 扣 focal、顯 PE)保留為其他 level 的 fallback path(目前無實際使用,純粹兼容)
- **sentinel 一視同仁**(2026-05-24 起):兩處改一致對待:
  - **Modal**:`cluster_json` 加 `sentinel` 欄,app.js `_renderTickerChips` ticker 列表 + `_computeClusterSeries`(加權指數 + 三大法人)+ `_tcSortedClusters`(modal nav metric)全納入 sentinel。`baseTv` 仍 focal-only(題材「熱度」基線,cluster 頁外層 TV sort 用)。
  - **選股雷達**:`build_focus_stock_page` 拿掉 sentinel-only-C 分支,sentinel 與 focal 同走全條件(出量 / 新高 / 成長 / 籌碼 / 潛力 A/B/C)。`_compute_yesterday_intersect` 也納入昨日 sentinel。原本「避免下跌股污染」的保守設計改為「下跌股的法人進場 / 成長 YoY 等訊號也值得列入」。

## 本地操作

```bash
uv sync
uv run python scripts/generate_html.py     # 重生 HTML (Supabase 偶有 connection pool 耗盡 → 重試)
open docs/index.html                        # 本機檢視
gh workflow run "Publish daily site"        # 手動觸發 CI 部署
bash scripts/deploy_db_proxy_public.sh      # Edge Function redeploy
```

## Commit 前 checklist(自我審計)

每次 commit 之前 mental walk-through:

- [ ] 改動的檔案在 SYSTEM.md「異動觸發表」內嗎?是 → 同 commit 更新本 repo 的
  `.claude/CLAUDE.md` / `README.md`(pre-commit hook 只認這兩個)。若該改動也需更新
  SYSTEM.md 的 section → SYSTEM.md 在 `StockGG-ingest` repo,依下方「跨 repo 溝通機制」
  產生 copy-paste prompt 給 user,由 user 貼到 ingest session 處理
- [ ] 改了 `generate_html.py` 的 `conn.fetch/fetchrow/fetchval`?是 → 同步擴
  `supabase/functions/db-proxy-public/index.ts` 的 `ALLOWED`,並 redeploy
- [ ] 改了 CSS 或 HTML 結構?是 → 本機 `uv run python scripts/generate_html.py`
  + Playwright / `open docs/index.html` 親眼看一次
- [ ] 改了 chart / lazy-load 相關?是 → 確認 `docs/history.json` 也一併 regen
- [ ] **commit 前必檢**:`grep -c "<<<<<<<" docs/index.html` 必須是 0(2026-05-25 踩第二次:P2 commit `88af34fc` 帶 marker 進 inline script;後續 bot regen 雖乾淨,Cloudflare Workers Static Assets 卻把髒版 stuck 在邊緣不重傳。`scripts/generate_html.py` 已加 `<!-- build {build_stamp} -->` UTC timestamp 讓每次 regen 的 HTML hash 必定變化迫使 wrangler 重傳,但 marker 本身仍要在 commit 前 grep 防呆)
- [ ] 改了 SEO meta?是 → 用 Twitter Card Validator / FB Debugger 看 preview
- [ ] Python fstring 內寫 JS:`\n` / `\r` / `\t` / `{`、`}` 都要雙化(`\\n`、`{{`、`}}`),且 inline `onclick="..."` attribute **外層用單引號** `'...'`、內層 `json.dumps()` 用 `"..."` 才不會撞引號嵌套 SyntaxError
- [ ] Pre-commit hook 跑通沒?沒看到 ✋ 提醒就過了 = 改動非結構性
- [ ] CSS 寫 `display:flex/inline-block/...` 時,如果該 element 預期用 `hidden` 屬性控顯隱,要加一條 `.foo[hidden]{display:none}` 對齊特異性(預設 UA `[hidden]` 規則會被 class CSS 蓋掉)
- [ ] hot-fix push 完後 → `gh workflow run "Publish daily site"` 觸發 deploy(push 不會自動觸發)

## 跨 repo 溝通機制(2026-05-18 改版,廢棄 INBOX)

> **背景**:原本透過 `~/Desktop/.iia-coord/INBOX.md` 同步,但該檔在兩 repo cwd 之外,
> Claude Code 對越界寫入有獨立沙箱確認,無法被 `allow Write(*)` 吸收 → 寫 INBOX
> 會陷入「越界提示 → 失敗 → retry」迴圈,卡住 session。改為 prompt 傳遞。

**規則**:當本 session 完成任務後發現「另一 repo 也需要動」,做兩件事:
1. **本 repo 該做的**先做完(commit + push)
2. **另一 repo 該做的**:不要自己跨 repo 寫檔。產生一段給 user 的 copy-paste prompt,內含:
   - 觸發來源(本 repo commit hash)
   - 任務描述(直接可執行的步驟)
   - 涉及檔案(完整絕對路徑)
   - 期望結果(commit + push,並回報 hash 給本 session)

**prompt 格式範本**:

```
[從 stockgg session 轉達]

觸發來源: stockgg commit <hash>
背景: <一句話說明>

請在 ingest repo (~/Desktop/StockGG-ingest) 執行:
1. 開檔 <絕對路徑>
2. <具體修改描述,連可貼的 diff 或新內容都附上>
3. commit + push (commit message 建議: "...")
4. 回報 commit hash

完成後我會在 stockgg 這邊繼續 <後續工作>。
```

**為何不再用檔案佇列**:越界寫入沙箱問題、無法保證寫得進去、user 體驗差(被反覆問權限)。
Prompt 傳遞由 user 主動切換 session 觸發,雖多一步 copy-paste,但 100% 可靠。

## 待辦

- [ ] Custom domain(Phase 4.4,買域名後)
