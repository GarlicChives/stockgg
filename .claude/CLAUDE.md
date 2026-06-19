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
- `supabase/functions/db-proxy-public/index.ts` — Edge Function + SQL allowlist(目前 **43 條**,Q1–Q46;每個 Q 的 SQL/語意對照在 ARCHITECTURE.md)。Q43–Q46 已 **slug 參數化**(`where slug = $1`),pullback / breakout 共用同條;多策略由 generate_html loop 各帶 slug fetch。
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
