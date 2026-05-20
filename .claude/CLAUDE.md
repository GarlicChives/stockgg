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

- `scripts/generate_html.py` — 單檔 HTML 渲染(~3900 行,所有頁面邏輯 + 內嵌 CSS/JS)
- `src/analysis/focus_themes.py` — 題材叢集(純 Python);兩個函式:
  - `detect_industry_clusters(tw_top_volume)` — 普適 TV 累加(pan_sub 用);輸入 = `stocks_info` filter market='TW';自動 dedupe 同 focal set 為 merged cluster(`A & B & C`)
  - `detect_focus_clusters(seeds, focus_members)` — **v2**(hl_sub 用,2026-05-19,對齊 ingest `8f27ede`);seeds = is_focus_seed(rank≤300 AND chg>4.5%, Q16),focus_members = is_focus_member rows(Q15)。算法:同 sub 種子數 ≥ `FOCUS_MIN_SEEDS`(2) 才算熱門題材,題材成員 today 有交易者 chg > `FOCUS_SENTINEL_THRESHOLD`(-3) 入 `focal`、< 入 `sentinel`。**v1 廢**(2026-05-18 `bd85f1d` → 次日 `8f27ede` 撤,hot_seed / limit_hot_seed / volume_universe 機制完全移除)
- `src/utils/db.py` — async DB client(用 `SUPABASE_ANON_KEY` + `db-proxy-public`)
- `data/theme_dictionary.json` — statementdog 主產業 / 子產業階層字典(2026-05 改 schema:ticker-centric `stocks` 物件,純台股;由 ingest 端 `scrape_statementdog_industries.py` 產生再 sync 到本 repo)。**main='近一年焦點'** 是 ingest 端人工編彙的長線觀察題材(62 sub / 230 ticker;sub 名稱「前綴·後綴」可用 「·」 split 群組),公開站「熱門題材」頁有獨立 sub-tab「🌟 焦點」,跟「📊 泛分類」(原 statementdog 47 main) 並陳
- `supabase/functions/db-proxy-public/index.ts` — Edge Function 含 SQL allowlist(目前 **20 條**):
  - Q1-Q8 日報基本資料、**Q9 v2** catalyst_events ±14/21d + `visible = TRUE` filter(ingest `4d5e7cc` 起;遠期 events visible=false 不出,daily cron 隨日期 flip true)、Q10 market_notes
  - Q11 theme_history 180→**400 days** retention
  - Q12 stock_meta(公司基本面快照)
  - Q13 ticker_close_history 400 天讀取(讓近一年焦點 cluster chart modal 能畫加權指數,因 theme_history 沒此 main 的 row)
  - Q14 special rows(處置 / 漲跌停 not in top-50)WHERE `extra->>'is_special'='true'`
  - **Q15 v2** focus_member rows(ticker 屬「近一年焦點」字典任一 sub 且 today 有交易,ingest `8f27ede` 起;v1 是 `is_volume_universe`,次日撤)WHERE `extra->>'is_focus_member'='true'`
  - **Q16** focus_seed ticker list(rank ≤ 300 AND chg > 4.5%, ingest `8f27ede` 預計算)WHERE `extra->>'is_focus_seed'='true'`
  - **Q17** ticker_net_inst_history 攤平歷史 net_inst (NTD = T86/3insti × close, ingest `ed3b2e9` 起;取代從 `theme_history.focal_breakdown` 反向索引建 ticker_net_inst 的舊 path — 對「純近一年焦點 ticker」focal_breakdown 永遠缺、反向索引拿不到)
  - **Q18 / Q19 / Q20** 主動式 ETF(ingest `f5faa21` → `edc8d49` v2):Q18 `active_etf_meta` master 按 AUM desc;Q19/Q20 v2 加 has_baseline CTE — DB 內該 ETF 只 1 day holdings 時 lots_chg/action = NULL,UI 顯警示「無前日 baseline」+ chip 不渲(避免硬標 new 失真);Q20 多 pct_of_float(lots × 1000 / stock_meta.shares_outstanding × 100)
- `.github/workflows/market_briefing.yml` — render + deploy(07:30 / 18:15 / 23:15 TW cron + repository_dispatch)。**push 不會觸發**,hot-fix 後要 `gh workflow run "Publish daily site"` 手動跑。`concurrency: publish-daily-site` 同 workflow 排隊不互相取消;commit-and-push step 含 `-X ours` rebase retry x3,避免本地 dev push 與 bot 撞 race。必須 `git add docs/index.html docs/history.json` 兩檔(漏 history.json 會在 rebase retry path 卡 unstaged changes,2026-05-19 踩過);wrangler-action wranglerVersion 必 pin 具體版本(`"4"` 浮動會撈到 4.86.0 撞 npx prompt 卡住,2026-05-20 踩過)
- `docs/index.html` — 渲染輸出(generate_html.py 寫入,bot CI push)
- `docs/history.json` — chart modal 用的歷史 payload,~5MB,含:
  - `history`: theme_history rows({"main||sub": [{d, s:{ticker:[tv,chg,close,net_inst,shares,**volume**]}}, ...]})(6-tuple,volume 是 2026-05-18 起加的)
  - `index`: TWII + TPEX 指數
  - `ticker_close`: per-ticker 400 天 close+shares(Q13)
  - `ticker_net_inst`: per-ticker daily net_inst(跨 main 反向索引,給近一年焦點 cluster 用)
- `wrangler.jsonc` — `assets.directory: docs` → Workers 整個 docs/ 當靜態 asset 服務

## 前端架構速覽

- **單頁 SPA + main tab + sub tab**:
  - main tab(top nav):熱門題材(首頁)/ 焦點股 / 主動式 ETF / 市場話題 / 國際金融
  - 熱門題材內 sub-tab:🌟 焦點(`hl_sub` level,展示 main='近一年焦點' cluster + 前哨 section)/ 📊 泛分類(`pan_sub` level,原 statementdog 47 main)
  - 兩 sub-tab 共用 cluster card 排行版型,各自獨立 sort state(`_clusterSort[level]`)
- **inline payload**(HTML script tag 內):
  - `IIA_CLUSTERS.hl_sub` / `IIA_CLUSTERS.pan_sub`(各 sub-tab 的 cluster + focal ticker)
  - `IIA_RADAR`(每檔 ticker 的 5 維 metric 與全焦點股平均,modal radar chart 用)
  - `artModalData`(各 ticker 的 analyst consensus + 公司介紹 HTML 片段)
- **lazy fetch**:`history.json`(modal chart 開啟才 fetch,no-cache 強制 revalidate),`unpkg lightweight-charts`(同上)
- **互動點**:
  - 廣泛概念股 chip 濾除(universal toggle)→ FLIP 動畫重排 cluster(threshold:cluster 數 >20 用 >3,否則 >1)
  - 外層 sort chip(成交金額/平均漲跌/平均乖離/平均 PE/平均殖利率/平均 β)→ per sub-tab state,重複點切 desc/asc
  - 內層 cluster header badge(漲跌/乖離/PE/殖利/β)→ per-cluster focal pill 排序,setFocalSort(cardId, key);預設 chg desc
  - chart 時間粒度 chip(1M/3M/6M/1Y/ALL)→ 過濾 series 後 rebase to 100;1Y 維度需要 ticker_close_history 400 天 backfill 完整
  - chart modal:左欄 ticker 列表 (vertical, by tv desc, 可 disable),右欄兩 chart 對齊(共用 priceScale minimumWidth) + 雙向 crosshair sync + 開啟動畫 + 三大法人 daily/cumulative 切換
  - modal radar chart(5 維 vs 焦點股平均)、CSV 下載、site search、share button
- **chip 系統**(2026-05-18 ingest 5a172be 起):
  - `.sp-tag.tag-strict` 嚴處 紅底(`punish_type='strict'`)
  - `.sp-tag.tag-punish` 處 橘底(`punish_type='normal'`)
  - `.sp-tag.tag-limit-up` 漲 紅底
  - `.sp-tag.tag-limit-down` 跌 綠底
  - 共用 `_flag_chips(info)` helper,_stk_pill + rank_rows_html 都用
- **rank=NULL handling**:special row(rank=NULL,extra.is_special=true)在 ranking table 顯「—」+ chip
- **前哨 section**(hl_sub cluster 才有,2026-05-19 v2 規格):由 `detect_focus_clusters` 提供 `cluster.sentinel`(題材成員 today 交易者依 `FOCUS_SENTINEL_THRESHOLD` (-3%) 切;chg > -3 入 focal、< 入 sentinel)。chip 用 `_stk_pill` 顯漲跌%(跟 focal pill 同樣式,加 `data-sentinel="1"` 區隔)。inline toggle button 在 focal pills 末段,點開後 panel max-height + opacity 動畫展開(`.cluster-sentinel-stocks[hidden]` 配 `toggleSentinelInline()`)。**舊版**(theme_dictionary 內該 sub 的完整 ticker list 扣 focal、顯 PE)保留為其他 level 的 fallback path(目前無實際使用,純粹兼容)

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
- [ ] **commit 前必檢**:`grep -c "<<<<<<<" docs/index.html` 必須是 0(歷史上踩過 git stash autostash 留 marker 進 inline script 導致 SyntaxError 整個頁面壞掉)
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
