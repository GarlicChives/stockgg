# Phase 0.1-0.2 — Public Render Path Query Inventory

Source: `scripts/generate_html.py` (lines current as of commit `c4ee6ca7`).
Other modules on the public render path: `src/analysis/focus_themes.py`
(no direct DB calls, takes data via arguments).

**Classification rule**:
- PUBLIC = derived from public sources (yfinance, Fed, TWSE/TPEX) OR
  Gemini-produced commentary (analysis_reports.raw_response,
  market_notes_json, catalyst_events.preview_text). Safe to expose.
- PRIVATE = raw subscription article content OR podcast transcripts
  (or fields derived directly from them with no rewriting). Must NOT
  reach a commercial public deploy.

| # | Line | Table | Columns | Filter | Class | Notes |
|---|------|-------|---------|--------|-------|-------|
| 1 | 880 | `analysis_reports` | `report_date, raw_response, market_notes_json` | `ORDER BY report_date DESC LIMIT 1` | PUBLIC | Gemini-derived. Safe. |
| 2 | 888 | `market_snapshots` | `symbol, close_price, change_pct, snapshot_date, extra` | latest per symbol | PUBLIC | yfinance |
| 3 | 909 | `trading_rankings` | `MAX(rank_date)` | `market='US'` | PUBLIC | yfinance |
| 4 | 912 | `trading_rankings` | `MAX(rank_date)` | `market='TW'` | PUBLIC | TWSE/TPEX |
| 5 | 917 | `trading_rankings` | `ticker, name, trading_value, change_pct, extra` | US top 30 today | PUBLIC | |
| 6 | 927 | `trading_rankings` | `ticker, name, trading_value, change_pct, is_limit_up_30m, extra` | TW top 30 today | PUBLIC | |
| 7 | 942 | **`articles`** | `id, source, title, published_at, tickers, COALESCE(refined_content, content)` | tickers ∈ top-30 ∩ last 60 days | **PRIVATE** | `content` is raw copyrighted article body; `refined_content` is Gemini summary but the SELECT can fall back to `content`. **Powers the stock modal's "📰 相關文章" section.** |
| 8 | 985 | **`articles WHERE source LIKE 'podcast_%'`** | `source, title, published_at, refined_content` | per-source latest 3 with valid tags | **PRIVATE** | Refined podcast notes — derivative of copyrighted transcripts. **Powers 股市筆記 → Podcast 筆記 tab.** |
| 9 | 1032 | `trading_rankings` | `ticker, change_pct` | watch tickers, latest | PUBLIC | |
| 10 | 1089 | `trading_rankings` | `ticker, name, change_pct, market` | market_notes tickers not in top-30 | PUBLIC | |
| 11 | 1108 | `catalyst_events` | `id, event_date, event_type, ticker, market, title, importance, preview_text` | next 21 days | PUBLIC | yfinance earnings + manual macro events + Gemini-written `preview_text` |

## Summary

- **9 PUBLIC queries** — safe under restricted credentials.
- **2 PRIVATE queries** (Q7, Q8) — must be removed/refactored before the
  public repo can run under a restricted role.

## Downstream impact of removing Q7 + Q8

Q7 (line 942) feeds `ticker_arts` → used in:
- `build_focus_html(..., ticker_arts, ...)` — populates the modal that
  shows "📰 相關文章" for each focal stock in the 熱門題材 tab.
- The whole modal's "📰 相關文章" section disappears.

Q8 (line 985) feeds `podcast_rows` → used in:
- `build_notes_html(..., podcast_rows, ...)` — the 股市筆記 → Podcast 筆記
  per-source accordion / list.
- The whole "🎙 Podcast 筆記" block in 股市筆記 tab disappears.

## Side note: `_normalize_ticker` + theme_dictionary lookup

Lines 1066-1079 read `data/theme_dictionary.json` from disk (not DB).
This is user-curated reference data — PUBLIC. No restriction needed.
