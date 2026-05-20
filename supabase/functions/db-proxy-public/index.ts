// db-proxy-public — restricted Edge Function for the public-facing site.
//
// Accepts queries from the public StockGG repo and forwards them to Postgres
// via the function's own internal connection (full privileges inside the
// function; the gate is the allowlist below).
//
// Differs from db-proxy:
//   * Hard SQL allowlist — only the 9 query templates the public site needs.
//   * Anything else returns 403, even with a valid Supabase JWT.
//
// Caller auth: the gateway requires a valid Supabase JWT (anon or service).
// The function does NOT trust JWT scope — it trusts only the allowlist.
//
// Adding a new permitted query: extend ALLOWED. Each entry is the
// whitespace-normalized lowercase form of the exact SQL the public repo will
// issue. See migration/queries_inventory.md in the StockGG repo for the
// source of truth.

import postgres from "npm:postgres"

let sql: ReturnType<typeof postgres> | null = null

function getDb() {
  if (!sql) {
    sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, {
      ssl: "require",
      max: 3,
      idle_timeout: 20,
      connect_timeout: 10,
    })
  }
  return sql
}

const ALLOWED: Set<string> = new Set([
  // Q1 — latest analysis_reports row (raw_response + market_notes_json)
  "select report_date, raw_response, market_notes_json from analysis_reports order by report_date desc limit 1",

  // Q2 — per-symbol latest market_snapshots
  "select distinct on (symbol) symbol, close_price, change_pct, snapshot_date, extra from market_snapshots where close_price is not null order by symbol, snapshot_date desc",

  // Q3 / Q4 — latest rank_date per market
  "select max(rank_date) from trading_rankings where market='us'",
  "select max(rank_date) from trading_rankings where market='tw'",

  // Q5 — US top 50 today (includes close_price; LIMIT bumped 30→50 2026-05)
  "select row_number() over (order by trading_value desc nulls last)::int as rank, ticker, name, trading_value, change_pct, close_price, extra from trading_rankings where rank_date=$1 and market='us' order by trading_value desc nulls last limit 50",

  // Q6 — TW top 50 today (includes close_price + is_limit_up_30m)
  "select row_number() over (order by trading_value desc nulls last)::int as rank, ticker, name, trading_value, change_pct, close_price, is_limit_up_30m, extra from trading_rankings where rank_date=$1 and market='tw' order by trading_value desc nulls last limit 50",

  // Q7 — change% for watch tickers (focus theme watch list)
  "select distinct on (ticker) ticker, change_pct from trading_rankings where ticker = any($1::text[]) order by ticker, rank_date desc",

  // Q8 — name + change% + market for market_notes tickers not in top-30
  "select distinct on (ticker) ticker, name, change_pct, close_price, market from trading_rankings where ticker = any($1::text[]) order by ticker, rank_date desc",

  // Q9 — catalyst events window: past 14 days through next 21 days,
  // 加 visible filter (ingest commit 4d5e7cc 起):遠期 events 在
  // visibility 範圍外時 visible=false 不出公開站,日期接近時 daily cron
  // 自動 flip true。SELECT 也含 visible 以保持兩端 SELECT 列一致。
  "select id, event_date, event_type, ticker, market, title, importance, preview_text, visible from catalyst_events where visible = true and event_date >= current_date - interval '14 days' and event_date <= current_date + interval '21 days' order by event_date, importance desc, ticker",

  // Q10 — most recent market_notes_json (decoupled from Q1: raw_response and
  // market_notes_json live in the same row but are written ~10h apart, so the
  // latest row often has a NULL market_notes_json before 18:00 TW)
  "select report_date, market_notes_json from analysis_reports where market_notes_json is not null order by report_date desc limit 1",

  // Q11 — theme_history past 400 days for given (main, sub) composite keys.
  // (2026-05-17 起從 180 改 400 對齊 ticker_close_history retention)
  // Composite filter via "main||sub" string ANY($1::text[]) so JS-side can
  // pass an array of keys derived from currently-rendered clusters.
  "select rank_date, main_industry, sub_industry, focal_count, focal_breakdown, total_tv, avg_chg_pct from theme_history where main_industry || '||' || sub_industry = any($1::text[]) and rank_date >= current_date - interval '400 days' order by main_industry, sub_industry, rank_date",

  // Q12 — stock_meta 公司基本面快照(由 ingest 端 src/news/stock_meta.py
  // 週更新寫入)。一次查多檔焦點股的完整 metadata 供:加權指數計算、
  // cluster PE/yield/beta 平均、pill 52w 位置%、modal 公司介紹 section
  "select ticker, name_zh, name_en, sector, industry, description, website, employees, shares_outstanding, float_shares, market_cap, pe_ttm, pe_forward, pb, eps_ttm, eps_forward, book_value, dividend_yield, last_dividend, ex_dividend_date, week52_high, week52_low, beta from stock_meta where ticker = any($1::text[])",

  // Q13 — ticker_close_history 過去 400 天 daily close + shares_outstanding。
  // 公開站 cluster chart modal 加權指數計算的「真資料源」(替代 focal_breakdown
  // 5-tuple 內的 close/shares,因為 focal_breakdown 只有當日進 top-50 的
  // ticker;近一年焦點 main 整批沒進 top-50 的 ticker 用這張表才拿得到歷史)。
  // ingest 端 src/news/stock_meta.py + scripts/backfill_ticker_history.py 寫入。
  "select ticker, rank_date, close, shares_out, volume from ticker_close_history where ticker = any($1::text[]) and rank_date >= current_date - interval '400 days' order by ticker, rank_date",

  // Q14 — special rows(處置 / 漲跌停)not in top 50。ingest 5a172be 起把這些
  // ticker 也寫進 trading_rankings(rank=NULL,extra.is_special='true');
  // Q6 只回 LIMIT 50 by TV 漏掉它們,Q14 補抓讓 cluster detection 抓得到
  // 被動元件 同題材的 3026 / 2492 等(沒進 top-50 但仍進 cluster)。
  "select ticker, name, trading_value, change_pct, close_price, is_limit_up_30m, extra from trading_rankings where rank_date=$1 and market='tw' and extra->>'is_special' = 'true' order by ticker",

  // Q15 — focus_member rows (ingest 8f27ede / v2 規格 2026-05-19 起):
  // ticker 屬「近一年焦點」main 任一 sub。涵蓋三個 bucket 的並集:
  //   - top-N (rank 1..300, rank IS NOT NULL)
  //   - special (rank=NULL, is_special=true)
  //   - focus_extra (rank=NULL,題材成員今日有交易但不在 top-N / special)
  // ingest 寫入時對 focus 字典內 ticker 都標 is_focus_member=true。
  // 公開站「焦點」tab 用這個 query 拿題材成員 today 交易資料,分 focal
  // (chg > -3%) / sentinel (chg < -3%) 顯示。
  // 廢:v1 is_volume_universe(2026-05-18 commit bd85f1d, 隔天 8f27ede 撤)。
  "select ticker, name, trading_value, change_pct, close_price, is_limit_up_30m, extra from trading_rankings where rank_date=$1 and market='tw' and extra->>'is_focus_member' = 'true' order by ticker",

  // Q16 — focus_seed ticker list (ingest 8f27ede / v2 規格 2026-05-19 起):
  // rank ≤ 300 AND change_pct > 4.5% 預計算種子。供「焦點」tab detection
  // step 1 反查題材字典,累計 sub 種子計數 ≥ 2 才算熱門題材。只需 ticker
  // (其他欄位走 Q15 拿)。注意:seed 不一定是 focus_member(條件不同)。
  "select ticker from trading_rankings where rank_date=$1 and market='tw' and extra->>'is_focus_seed' = 'true' order by ticker",

  // Q17 — ticker_net_inst_history 攤平歷史 net_inst (NTD,T86/3insti × close)。
  // 取代 stockgg 端從 theme_history.focal_breakdown 反向索引拿 ticker_net_inst
  // 的舊 path。解「純近一年焦點 ticker(從沒進 universe)歷史 net_inst 永遠空」
  // (見 ingest SYSTEM.md Gotcha #19)。Ingest commit ed3b2e9 起寫入,
  // 對「近一年焦點」字典 ~322 ticker × 400 day 寫滿。
  "select ticker, rank_date, net_inst from ticker_net_inst_history where ticker = any($1::text[]) and rank_date >= current_date - interval '400 days' order by ticker, rank_date",

  // Q18 — 主動式 ETF master list(2026-05-20 對應 ingest f5faa21)。
  // 「主動式 ETF」頁 tab 按 aum_ntd desc 排序;每檔 ETF 一個 tab。
  "select etf_code, etf_name, short_name, issuer, aum_ntd, nav_per_unit, units_outstanding, listing_date, expense_ratio, fund_url from active_etf_meta order by aum_ntd desc nulls last, etf_code",

  // Q19 v2 — 某 ETF 最新交易日 holdings + 對前一交易日 diff,加 baseline check
  // (對應 ingest edc8d49):若 DB 該 ETF 只有 1 day holdings → has_baseline=FALSE
  // 整批 lots_chg / action = NULL,前端不渲染 chip,顯警示「無前日 baseline」。
  "with last_two as (select distinct holding_date from active_etf_holdings where etf_code = $1 order by holding_date desc limit 2), has_baseline as (select count(*) >= 2 as yes from last_two), latest as (select max(holding_date) as d from last_two), prev as (select min(holding_date) as d from last_two where holding_date < (select d from latest)) select coalesce(t.ticker, y.ticker) as ticker, coalesce(t.name, y.name) as name, t.lots, t.weight_pct, t.market_value_ntd, t.market, t.is_cash, y.lots as prev_lots, case when (select yes from has_baseline) then coalesce(t.lots, 0) - coalesce(y.lots, 0) else null end as lots_chg, (select yes from has_baseline) as has_baseline, case when not (select yes from has_baseline) then null when t.lots is null or t.lots = 0 then 'exit' when y.lots is null or y.lots = 0 then 'new' when t.lots > y.lots then 'add' when t.lots < y.lots then 'reduce' else 'hold' end as action from (select * from active_etf_holdings where etf_code = $1 and holding_date = (select d from latest)) t full outer join (select * from active_etf_holdings where etf_code = $1 and holding_date = (select d from prev)) y on t.ticker = y.ticker order by t.weight_pct desc nulls last",

  // Q20 v2 — 某個股被哪些主動 ETF 持有 + diff + 佔流通比重 + per-ETF baseline check
  // (對應 ingest edc8d49):若 DB 該 ETF 只有 1 day holdings → has_baseline=FALSE
  // 該 row 的 lots_chg / action = NULL,modal 端 chip 不渲染。
  "with last_two as (select etf_code, holding_date, row_number() over (partition by etf_code order by holding_date desc) as rn from active_etf_holdings where ticker = $1), baseline_per_etf as (select etf_code, max(rn) >= 2 as yes from last_two group by etf_code), latest_per as (select etf_code, holding_date from last_two where rn = 1), prev_per as (select etf_code, holding_date from last_two where rn = 2) select m.etf_code, m.etf_name, m.short_name, m.issuer, m.aum_ntd, t.holding_date, t.lots, t.weight_pct, t.market_value_ntd, y.lots as prev_lots, coalesce(bp.yes, false) as has_baseline, case when coalesce(bp.yes, false) then coalesce(t.lots, 0) - coalesce(y.lots, 0) else null end as lots_chg, round(t.lots * 1000.0 / nullif(sm.shares_outstanding, 0) * 100, 3) as pct_of_float, case when not coalesce(bp.yes, false) then null when t.lots is null or t.lots = 0 then 'exit' when y.lots is null or y.lots = 0 then 'new' when t.lots > y.lots then 'add' when t.lots < y.lots then 'reduce' else 'hold' end as action from active_etf_meta m left join baseline_per_etf bp on bp.etf_code = m.etf_code left join active_etf_holdings t on t.etf_code = m.etf_code and t.ticker = $1 and (t.etf_code, t.holding_date) in (select etf_code, holding_date from latest_per) left join active_etf_holdings y on y.etf_code = m.etf_code and y.ticker = $1 and (y.etf_code, y.holding_date) in (select etf_code, holding_date from prev_per) left join stock_meta sm on sm.ticker = $1 where t.lots is not null or y.lots is not null order by m.aum_ntd desc nulls last",
])

function normalize(q: string): string {
  return q.trim().toLowerCase().replace(/\s+/g, " ")
}

Deno.serve(async (req: Request) => {
  let body: { query: string; params?: unknown[] }
  try {
    body = await req.json()
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    })
  }

  const { query, params = [] } = body
  if (!query || typeof query !== "string") {
    return new Response(JSON.stringify({ error: "Missing query" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    })
  }

  const norm = normalize(query)
  if (!ALLOWED.has(norm)) {
    return new Response(
      JSON.stringify({
        error: "Query not permitted by db-proxy-public allowlist",
        normalized_preview: norm.slice(0, 240),
      }),
      { status: 403, headers: { "Content-Type": "application/json" } },
    )
  }

  try {
    const db = getDb()
    const result = await db.unsafe(query, params as unknown[])
    const raw = result as unknown as { command: string; count?: number }
    const command = raw.command ?? ""
    const rowCount: number =
      typeof raw.count === "number"
        ? raw.count
        : Array.isArray(result)
        ? result.length
        : 0
    const tag =
      command === "INSERT"
        ? `INSERT 0 ${rowCount}`
        : `${command} ${rowCount}`
    const rows = Array.isArray(result) ? result.map((r) => ({ ...r })) : []
    return new Response(JSON.stringify({ rows, command: tag }), {
      headers: { "Content-Type": "application/json" },
    })
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    return new Response(JSON.stringify({ error: msg }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    })
  }
})
