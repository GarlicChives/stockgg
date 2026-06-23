# Project: stockgg (public daily-briefing site)

> **新 session 開頭**:讀 `~/Desktop/StockGG-ingest/SYSTEM.md`(兩 repo 全景:資料流、排程、職責、踩坑)。
> 本檔只覆蓋 stockgg 自己的 do/don't,且**刻意精簡**;關鍵檔案的完整說明、SQL allowlist Q1–Q46
> 對照、前端互動細節、各功能設計根因與變更史 → 全在 `ARCHITECTURE.md`(按需讀,不常駐 context)。

## 本 repo 角色

Thin presentation layer。只渲染 HTML + 部署 Cloudflare Workers。資料攝取 / AI 分析 / 爬蟲全部在
companion repo `StockGG-ingest`(本機 `~/Desktop/StockGG-ingest`,私有)跑。
**Repo 2026-06-10 起 PUBLIC**(對全世界可見)——嚴禁任何 secrets 進 repo,commit 前多想一秒。

## 嚴格 guardrail

| 規則 | 為什麼 |
|---|---|
| ❌ **不要引入** `SUPABASE_SERVICE_ROLE_KEY` 到本 repo | service_role 是私有 repo 專用;本 repo 用 anon |
| ❌ **不要呼叫** Gemini / OpenAI / 任何 LLM API | 公開 repo 沒任何 LLM key,也不該有 |
| ❌ **不要爬任何網站** | 法律隔離邊界,原始內容只能在私有 repo |
| ❌ **不要在這裡跑 LLM-generated 分析** | 分析是私有 repo 的事,這裡只讀已存的結果 |
| ✅ **新增或調整 query 就同步擴 allowlist** | 改 `supabase/functions/db-proxy-public/index.ts` 的 `ALLOWED`(normalize-比對是 exact match,整段改才會通過),然後跑 `bash scripts/deploy_db_proxy_public.sh`。不擴/不 redeploy 會 CI 403 |
| ✅ **不確定查詢能不能跑時,本機跑 generate_html.py 看 403** | 是最快的 sanity check |

## 鐵則速記(細節與根因見 `ARCHITECTURE.md`)

- **公開站永遠不空、永遠呈現最新完整交易日**(rank_date 查詢必帶 `AND rank IS NOT NULL`)。
- **漲跌% NULL 一律顯「—」**(neutral),絕不 fallback 成 0% 或當平盤(平盤 0 是另一回事)。
- **部署**:`git push` **不會**觸發 CI;hot-fix 後要 `gh workflow run "Publish daily site"`。
  本機手動部署**只能走 `bash scripts/deploy_site.sh`**(部署前斷言 index.html 完整、部署後 smoke-test
  線上 200)——**嚴禁裸跑 `wrangler deploy`**:index.html 是 gitignored 生成檔,沒先 generate 就部署
  會上傳缺 index.html 的空版本 → 全站根 404(2026-06-18 真因)。CI 已內建同款 guard。
  **勿一日連發多次 deploy**(production alias 會被搖亂)。站台前有 **Cloudflare Access**
  (email 白名單;未登入 production root 回 **302** 登入頁,不是 200)。站台壞掉先打
  **版本預覽 URL** `https://<versionId前8碼>-stockgg.v4578469.workers.dev/`(**繞過 Access**、
  直命中該版本 assets)判斷是 worker/版本問題還是 alias/Access 問題。smoke test 也測這個 URL。
- ⚠ **唯一部署路徑 = GitHub Actions「Publish daily site」或本機 `deploy_site.sh`**(兩者都先 generate
  再 deploy)。**Cloudflare Workers Builds 的 git 自動建置已於 2026-06-18 斷開,且永遠不要重連**:
  它每次 push 就 clone repo 跑 `wrangler deploy`,但**不會跑 `generate_html.py`** → index.html 是
  gitignored 缺檔 → 部署出**只有 app.js+style.css 的空版本** → 全站 404。這是「時好時壞」真兇
  (連 `chore: update daily report [skip ci]` bot commit 都會觸發,`[skip ci]` 擋得了 GitHub Actions
  擋不了 Workers Builds)。**判斷有沒有被偷部署**:`gh api repos/GarlicChives/stockgg/commits/<sha>/check-runs`
  若出現 `Workers Builds: stockgg [cloudflare-workers-and-pages]` 就是又被連上了。
- ⚠ **絕不在 Cloudflare dashboard 儲存/編輯這個 Worker**(Quick Edit、設定頁按 Save 等):同理 —
  dashboard 手上只有 worker script、沒有 asset manifest,一存就 deploy 出缺 index.html 的空版本
  (2026-06-18 設 Access 時踩到)。Access policy 在 Zero Trust 區改、別碰 Worker 本身。
  被洗掉就重跑 `bash scripts/deploy_site.sh`。
- **改 `generate_html.py` 的 `conn.fetch*` query** → 必同步擴 db-proxy allowlist + redeploy。
- **生成檔不 commit**(index.html / history.json / kline.json / bt_summary.json / bt_detail.json 全 gitignore,
  CI fresh regen 後只 deploy 不 commit)。

## 關鍵檔案(導航;完整說明見 `ARCHITECTURE.md`)

- `scripts/generate_html.py` — HTML 渲染主程式(~5000 行;頁面結構 + 所有 DB fetch + payload 組裝)。
- `docs/style.css` — 全站 CSS(直接編輯;改檔後 generate_html 自動 content-hash cache-bust `?v=`)。
- `docs/app.js` — 全站 JS(直接編輯;個股 modal 的 `artModalData` 等資料 const 仍 inline 在 index.html)。
- `src/analysis/focus_themes.py` — 題材叢集偵測(`detect_industry_clusters` 泛分類 / `detect_focus_clusters` v3 焦點,前綴群組化)。
- `src/utils/db.py` — async DB client(`SUPABASE_ANON_KEY` + `db-proxy-public`;內建 5xx retry)。
- `supabase/functions/db-proxy-public/index.ts` — Edge Function + SQL allowlist(目前 **44 條**,Q1–Q47;每個 Q 的 SQL/語意對照在 ARCHITECTURE.md)。Q43–Q46 已 **slug 參數化**(`where slug = $1`),各策略共用同條;Q47(`select slug from strategy_backtest_public order by slug`)= 策略模擬頁**動態 slug 清單**權威來源。多策略由 generate_html 先跑 Q47 撈全集、再 loop 各帶 slug fetch;新策略 ingest 寫 DB 即自動上架,本 repo 零改動(`STRAT_ORDER` 不再 hardcode,`_STRAT_PREFERRED_ORDER` 僅 Q47 失敗時 fallback、非清單閘)。**tab 顯示序 = 各策略 `payload.metrics.sharpe` 由高到低**(冠軍最左)。slug=`dashboard`(ingest `build_dashboard` 寫的聚合列)從策略 tab 剔除,單獨渲染為**「📊 總儀表板」第一個 sub-tab、預設 active**(`_build_dashboard_html`:① **各策略買進共識(單欄)**(=≥2 策略 watchlist 共選,本檔從各策略 watchlist 同源推導、不吃 ingest `consensus_picks`)+ 各策略明日買進標的卡格(`consensus_tk`=買進共識給 🤝、可點開個股 K線/主動ETF modal、雷達★高亮)。**2026-06-20 user 決議:總儀表板「明日賣出標的」(含共識賣出欄、明日分歧欄、各策略賣出卡格)不論出場分類一律完全移除**——不再讀 `strategies[].sells`、不做賣出/分歧推導,共識區由原三欄縮為單欄買進(賣出仍只在各策略獨立 tab 的逐筆呈現)。**2026-06-21:題材功能**(共用 module 級 `_theme_chips_html(tk,theme_map,hot_themes)`;`theme_map`=ticker→近一年焦點 sub 前綴去重、`hot_theme_set`=今日 `focus_hl_clusters` 前綴,皆主流程從 `highlight_subs`+`focus_hl_clusters` 推導後由 `build_trade_sim_page(theme_map=,hot_themes=)` 注入,**無新 DB query**):(a)**總儀表板買進列加「所屬題材」**(`區塊 2` 各策略明日買進卡的 chip 走 `grid-column:2/-1` 與代號名對齊;今日焦點題材 accent 高亮且排前。**共識個股 pill 已於 2026-06-23 移除題材**);(b)**共識區=各策略買進共識個股列表**(`.dash-cons-list`/`.dash-cons-pill`;**2026-06-23 user 還原:矩陣 → 個股列表、移除共識題材**)。≥2 策略共選同一檔=買進共識(`_buy_by`→`buy_consensus`),每 pill 一格:第一列「代號名 + 股價(漲跌%)」(`tk_px`/`tk_chg`,漲紅跌綠)、第二列「買它的策略 chip」(`_cons_pill` 內 `_sname(b)`);**不含任何題材字樣**;可點開個股 modal、雷達★高亮(`.dash-hot`)。〔史:2026-06-21~22 曾為 `.dash-cmx` 策略×共識矩陣(欄=策略、列=共識個股+共識題材、表頭凍結 `--cmx-head-top`、首欄可拖曳 `--cmx-label-w`、格內 `data-px`+💰可投入金額試算、題材列點開走勢 modal),2026-06-23 整組下架——`dash-cmx*` CSS / `dashCalcBudget`/`dashCmxResize`/`dashCmxStickyTop` JS / `IIA_CLUSTERS.cons` / 共識題材計算(`_th_strats`/`consensus_themes`)/`_cell`/`_row_label` 全移除。〕(c)**各策略獨立 tab 的 `_build_trade_next_html` 明日買進卡也加題材列**(`.sim-next-themes`,同樣注入)。② 完整 9 指標 1 年回測績效比較表(表頭可排序、預設夏普降序、benchmark 釘底,9 指標 join 各 slug `payload.metrics`)**②.5 進場跳空 % 分布**(2026-06-21 user;`_build_entry_dist_html`,**總儀表板=全策略合併、各策略 tab=該策略**逐筆):3 張 1%-bin 直方圖(全部 / 最強20%贏家 / 最爛20%輸家)。進場跳空% = `_trade_entry_gap`=(隔日開盤進場價−前一交易日收盤)/前收×100(prev close 取自 `ticker_close_full`;進場價=回測 entry_price=隔日開盤);贏/輸家=**全體逐筆**依 `pnl_pct` 排序前/後 20%(非個股累積)。pairs 在 fetch loop 由 `_entry_pairs_from_detail(_by, ticker_close_full)` 算好存 `strat_data[slug]["entry_pairs"]`,dashboard 段彙整各策略 pairs。bar 紅=跳空上漲/綠=跳空下跌、三圖共用 x 軸。③ **共識買回測績效**(ingest a5a2336;slug `consensus_unlimited`=🅰️無限資金/`consensus_300m`(slug 沿用舊名)=🅱️1000萬資金,**子分頁 `showConsensusBtTab`** 切換,完全重用單一策略元件 `_build_backtest_html`+`_build_bt_trades_html`);讀 dashboard payload 同走 Q44 slug 參數化,缺則整 tab 隱藏。表頭排序 JS = `app.js dashSortPerf`。**非 tab slug**(`_NON_TAB_SLUGS`={dashboard,consensus_unlimited,consensus_300m})不長獨立策略 tab:dashboard 只讀 payload、consensus_* 仍進 fetch loop(`_fetch_slugs`)撈 Q44/45/46 寫 `bt_summary/detail_<slug>.json` 供段③渲染;`_activateStratData('dashboard')` 連帶激活當前 active 子分頁圖表/逐筆(避隱藏 pane 0 寬))。**gap(帶量跳空動能)頁有專屬釐清註記**(`.sim-next-gapclar`/`.sim-bt-pb-clar`):卡片 % = 當日「收盤漲幅」、規則「跳空 ≤+9%」指開盤隔夜跳空,兩者不同數值故收盤可達漲停不矛盾(僅此 slug)。
- `data/theme_dictionary.json` — statementdog 主/子產業字典(ticker-centric;`main='近一年焦點'` 給焦點 sub-tab)。
- `data/pullback_public.json` — 拉回買 1 年回測 **fallback 靜態檔**(主來源已改 DB Q44 `strategy_backtest_public`)。
- `.github/workflows/market_briefing.yml` — render + deploy workflow(cron 07:30/18:15/23:15 TW;push 不觸發)。
- `wrangler.jsonc` — Workers 靜態資產服務(`assets.directory: docs`);`not_found_handling:"single-page-application"`(根 404 護欄,**勿移除**)。
- 生成輸出 / lazy payload(全 gitignore、CI 上傳不 commit):`docs/index.html`、`docs/history.json`(~15MB)、`docs/kline.json`(~8MB,個股日 K)、`docs/bt_summary_<slug>.json`(回測 by_stock 100 檔卡+chart_trades)、`docs/bt_detail_<slug>.json`(by_ticker 全往返,點某檔開 modal 用)—— **per-slug**(pullback / breakout),前端切到該策略才 lazy-fetch 對應檔。

> **需要這些的細節就讀 `ARCHITECTURE.md`**:每個 Q 的 SQL 與設計根因、精選閘公式(`_distill_daily_clusters`)、crash/rally banner、kline「今日根錨定」、history/kline 的 cache-bust 踩坑、404 排查法全文、主動式 ETF 頁、產業地圖蜘蛛網、策略模擬頁(Q40–Q45 演進)、選股雷達 5 條件 / 潛力股 / 看高做低 / 品質濾網 / chip 系統 / 前哨 section、📈 趨勢頁 risk chip。

## 前端架構速覽(細節見 `ARCHITECTURE.md`)

- **單頁 SPA + main tab + sub tab**。main tab:熱門題材(首頁)/ 選股雷達 / 主動式 ETF / 🗺️ 產業地圖 / 市場話題 / 國際金融 / 🛡️ 風控 / 📈 策略模擬。
- 熱門題材內 sub-tab:🌟 焦點(`hl_sub`)/ 📊 泛分類(`pan_sub`),共用 cluster card 版型、各自獨立 sort state。
- **資料**:inline payload(`IIA_CLUSTERS` / `artModalData` 等)寫在 index.html;大檔(history / kline / bt_trades)+ `unpkg lightweight-charts` 走 lazy fetch(`no-cache`,不可加隨機 query)。
- 互動點(chip 濾除 / sort chip / chart modal / 個股 modal / 各 sub-tab 篩選條件 / 手機橫向溢出處理)→ ARCHITECTURE.md。

## 本地操作

```bash
uv sync
uv run python scripts/generate_html.py     # 重生 HTML (Supabase 偶有 connection pool 耗盡 → 重試)
open docs/index.html                        # 本機檢視
gh workflow run "Publish daily site"        # 手動觸發 CI 部署(優先用這個)
bash scripts/deploy_site.sh                 # 本機手動部署(內建 deploy guard;勿裸跑 wrangler deploy)
bash scripts/deploy_db_proxy_public.sh      # Edge Function redeploy
```

## Commit 前 checklist(自我審計)

每次 commit 之前 mental walk-through:

- [ ] 改動的檔案在 SYSTEM.md「異動觸發表」內嗎?是 → 同 commit 更新本 repo 的
  `.claude/CLAUDE.md` / `README.md`(pre-commit hook 只認這兩個)。需動 SYSTEM.md(在 ingest repo)
  → 依下方「跨 repo 溝通機制」產 prompt 給 user。
- [ ] 改了 `generate_html.py` 的 `conn.fetch/fetchrow/fetchval`?是 → 同步擴
  `db-proxy-public` 的 `ALLOWED` 並 redeploy。
- [ ] 改了 CSS 或 HTML 結構?是 → 本機 `uv run python scripts/generate_html.py` + 親眼看一次。
- [ ] 改了 chart / lazy-load 相關?是 → 確認對應 payload 檔(history.json / kline.json / bt_summary.json / bt_detail.json)也一併 regen。
- [ ] **commit 前必檢**:`grep -rc "<<<<<<<" scripts/generate_html.py docs/app.js docs/style.css` 各須是 0
  (generate_html 已加 `<!-- build {build_stamp} -->` 讓每次 regen HTML hash 必變,迫使 wrangler 重傳)。
- [ ] 改了 SEO meta?是 → 用 Twitter Card Validator / FB Debugger 看 preview。
- [ ] Python fstring 內寫 JS:`\n`/`\r`/`\t`/`{`/`}` 都要雙化(`\\n`、`{{`、`}}`);inline `onclick="..."`
  **外層用單引號 `'...'`、內層 `json.dumps()` 用 `"..."`** 才不撞引號嵌套 SyntaxError。
- [ ] CSS 寫 `display:flex/...` 且該元素用 `hidden` 控顯隱時,要加 `.foo[hidden]{display:none}` 對齊特異性。
- [ ] hot-fix push 完後 → `gh workflow run "Publish daily site"` 觸發 deploy(push 不會自動觸發)。

## 跨 repo 溝通機制(廢棄 INBOX 檔案佇列;改 prompt 傳遞)

> 越界寫檔(`~/Desktop/.iia-coord/INBOX.md`)會觸發沙箱確認迴圈卡死 session,故改用 copy-paste prompt。

**規則**:本 session 完成任務後若發現「另一 repo 也需要動」:
1. **本 repo 該做的**先做完(commit + push)。
2. **另一 repo 該做的**:不要自己跨 repo 寫檔。產一段給 user 的 copy-paste prompt,內含:觸發來源
   (本 repo commit hash)、任務描述(可執行步驟)、涉及檔案(完整絕對路徑)、期望結果(commit + push
   並回報 hash)。

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

## 待辦

- [ ] Custom domain(Phase 4.4,買域名後)
