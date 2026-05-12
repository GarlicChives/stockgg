# Phase 0.3-0.4 — RLS Equivalent Restriction Design

Goal: ensure the public repo (and any compromised credentials it holds)
is **technically incapable** of reading subscription-article body text or
podcast transcripts.

Inputs: `queries_inventory.md` shows 2 PRIVATE queries (Q7 article
`content`/`refined_content`, Q8 podcast `refined_content`).

## Current infra constraint

`src/utils/db.py` talks to a Supabase **Edge Function** (`db-proxy`) over
HTTPS:443. The Edge Function presumably runs arbitrary SQL using a
service-role-class internal connection. We do NOT directly connect to
Postgres — direct port 5432 is blocked on the user's company WiFi.

**Implication**: Postgres-level RLS policies alone won't help if every
query still flows through one omnipotent Edge Function. We must
restrict at the Edge-Function layer.

## Two viable approaches

### Option A — Two Edge Functions, two bearer keys

- Keep current `db-proxy` (service_role internally) → **private repo only**
- New `db-proxy-public` (separate Edge Function) → **public repo only**
  - Accepts a new bearer key `SUPABASE_PUBLIC_KEY` (issue via Supabase
    Functions secret).
  - Either:
    - **A1**: hard-coded query allowlist (regex match against a small
      set of approved SQL statements). Simplest, hardest to bypass.
    - **A2**: forwards to Postgres but executes
      `SET LOCAL ROLE public_renderer` first, so all SQL runs under a
      Postgres role with column-level grants. Flexible but trusts the
      Postgres GRANT layer.

- **Pros**: Clear separation; key compromise only exposes the public
  function's capability.
- **Cons**: Two functions to maintain; A1 is rigid, A2 needs Postgres
  RLS/GRANT plumbing.

### Option B — Single Edge Function, key-based branching

- Modify existing `db-proxy` to detect which bearer key it receives:
  - `SUPABASE_SERVICE_ROLE_KEY` → unrestricted (current behavior)
  - `SUPABASE_PUBLIC_KEY` → enforce allowlist or `SET LOCAL ROLE`
- Pros: One function. Pros: Concentrated logic.
- Cons: A bug in the branching is a single point of failure that exposes
  everything.

### Recommendation: **Option A1** (two functions, hard allowlist)

Why:
- A1 is the easiest to audit. The allowlist literally enumerates every
  query the public repo is allowed to make. No semantic surprises.
- A bug in the Edge Function is contained: an `A1` malfunction =
  rejected query, not data exposure.
- Future query additions are explicit code changes to the public Edge
  Function — they become an obvious audit point.

## Allowlist for `db-proxy-public` (Option A1)

Derived from `queries_inventory.md` PUBLIC rows. Each entry is a
regex pattern + safety notes. The Edge Function rejects anything that
doesn't match an allowlist entry.

```
1. SELECT report_date, raw_response, market_notes_json FROM analysis_reports ORDER BY report_date DESC LIMIT 1
2. SELECT DISTINCT ON (symbol) symbol, close_price, change_pct, snapshot_date, extra FROM market_snapshots WHERE close_price IS NOT NULL ORDER BY symbol, snapshot_date DESC
3. SELECT MAX(rank_date) FROM trading_rankings WHERE market='US'
4. SELECT MAX(rank_date) FROM trading_rankings WHERE market='TW'
5. SELECT ROW_NUMBER() OVER (...) AS rank, ticker, name, trading_value, change_pct, extra FROM trading_rankings WHERE rank_date=$1 AND market='US' ORDER BY trading_value DESC NULLS LAST LIMIT 30
6. SELECT ROW_NUMBER() OVER (...) AS rank, ticker, name, trading_value, change_pct, is_limit_up_30m, extra FROM trading_rankings WHERE rank_date=$1 AND market='TW' ORDER BY trading_value DESC NULLS LAST LIMIT 30
7. SELECT DISTINCT ON (ticker) ticker, change_pct FROM trading_rankings WHERE ticker = ANY($1::text[]) ORDER BY ticker, rank_date DESC
8. SELECT DISTINCT ON (ticker) ticker, name, change_pct, market FROM trading_rankings WHERE ticker = ANY($1::text[]) ORDER BY ticker, rank_date DESC
9. SELECT id, event_date, event_type, ticker, market, title, importance, preview_text FROM catalyst_events WHERE event_date >= CURRENT_DATE AND event_date <= CURRENT_DATE + INTERVAL '21 days' ORDER BY event_date, importance DESC, ticker
```

Notes:
- Q1 `analysis_reports.raw_response` is Gemini-generated and the
  ground-truth source of the public site's morning briefing. Safe.
- Q1 `analysis_reports.market_notes_json` contains topic-level
  summaries + tickers; **does NOT include article body text** but DOES
  include 1-2-sentence "summary" and "key_points" per topic that derive
  from raw content. Gemini-rewritten, so safe per fair-use boundary.
- All Q5/Q6/Q7/Q8 over `trading_rankings` are pure public market data.
- Q9 over `catalyst_events` includes `preview_text` which is Gemini-
  generated. Safe.

## Items NO LONGER permitted under public key

(must be removed from public repo when Phase 3 lands; tracked here for
Phase 0.4 output)

1. `articles` — any column. The public render path's only legitimate
   read of `articles` was to power the "📰 相關文章" modal — that
   block is removed in Phase 3.6.
2. `articles.content`, `articles.refined_content`, `articles.embedding`
   — fully blocked.
3. `articles WHERE source LIKE 'podcast_%'` — the Podcast 筆記 tab
   block is removed in Phase 3.6.

## Phase 0.4 — code paths that break under the allowlist

Cross-referencing `queries_inventory.md`:

| Public repo file | Function | Breaks because | Phase 3 action |
|---|---|---|---|
| `scripts/generate_html.py:942` (`art_rows`) | `ticker_arts` build | reads articles.content | Delete the article-modal pipeline; modal keeps only "📊 機構目標價共識". |
| `scripts/generate_html.py:985` (per-source podcast loop) | `podcast_rows` build | reads podcast refined_content | Delete the 🎙 Podcast 筆記 block in `build_notes_html`. |
| `scripts/generate_html.py:1083+` (notes_tickers extra fetch) | `nr` query | reads trading_rankings only | **Stays** — already PUBLIC. |

Knock-on cleanups in Phase 3 (already in PROGRESS.md):
- `build_focus_html`'s `ticker_arts` param becomes unused → simplify signature.
- `build_notes_html`'s `podcast_rows` param becomes unused → simplify signature.

## Implementation plan (defer to Phase 3.1)

When Phase 1+2 are stable:

1. In Supabase Dashboard, generate a new function secret `PUBLIC_KEY` and
   distribute it to public repo (`SUPABASE_PUBLIC_KEY` env var).
2. Author `supabase/functions/db-proxy-public/index.ts` with an in-code
   allowlist matching the 9 patterns above. Reject everything else with
   HTTP 403.
3. Deploy with `supabase functions deploy db-proxy-public`.
4. Public repo's `src/utils/db.py` switches to the new endpoint URL
   + new key.
5. Run public repo CI/local build → fix every failure (each is a
   pre-tagged removal).
6. Keep both endpoints alive for 1 week as rollback safety net.

## Open questions

- **Q**: Will Supabase allow per-function secrets, or are all secrets
  shared per project?
  → Supabase Functions support function-scoped secrets via the
    `--env` flag of `supabase functions deploy`. OK.
- **Q**: Does the existing `db-proxy` need any changes during Phase 3?
  → No. It stays exactly as is, used only by the private repo.
- **Q**: Risk of regex-based allowlist being bypassed?
  → The allowlist matches the **complete normalized query string**
    (collapse whitespace, lowercase keywords). Variable bindings stay
    parametrized as `$1`. SQL injection via params is still
    Postgres's responsibility.
