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

  // Q9 — catalyst events window: past 14 days through next 21 days
  "select id, event_date, event_type, ticker, market, title, importance, preview_text from catalyst_events where event_date >= current_date - interval '14 days' and event_date <= current_date + interval '21 days' order by event_date, importance desc, ticker",

  // Q10 — most recent market_notes_json (decoupled from Q1: raw_response and
  // market_notes_json live in the same row but are written ~10h apart, so the
  // latest row often has a NULL market_notes_json before 18:00 TW)
  "select report_date, market_notes_json from analysis_reports where market_notes_json is not null order by report_date desc limit 1",

  // Q11 — theme_history past 180 days for given (main, sub) composite keys.
  // Composite filter via "main||sub" string ANY($1::text[]) so JS-side can
  // pass an array of keys derived from currently-rendered clusters.
  "select rank_date, main_industry, sub_industry, focal_count, focal_breakdown, total_tv, avg_chg_pct from theme_history where main_industry || '||' || sub_industry = any($1::text[]) and rank_date >= current_date - interval '180 days' order by main_industry, sub_industry, rank_date",
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
