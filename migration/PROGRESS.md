# Repo Split Migration — Progress Tracker

**Goal**: Split current repo into two:
- `iia-ingest` (private) — crawlers, Whisper, Gemini analysis, admin UI
- current repo → public, commercial-safe — pure HTML rendering from DB

**Started**: 2026-05-12.

---

## ⚠ Resume protocol (read FIRST if a session was interrupted)

1. Read this whole file top-to-bottom.
2. Find the **first step not marked ✅**. That's where work continues.
3. Each step lists exact command(s), expected result, and rollback.
4. After completing a step:
   - Mark its checkbox `- [x]` and add a `→` line with date + key result.
   - Update **"Cursor"** section below.
   - `git add migration/PROGRESS.md && git commit -m "migration(<phase>.<step>): <result>" && git push`
5. If blocked: mark step `- [!]` and write what's needed under **"Open blockers"**.
6. Do **NOT** skip steps or reorder phases unless explicitly authorized.

---

## Cursor

- **Current phase**: Migration COMPLETE. Only optional polish (4.4
  custom domain) remains.
- **Last completed step**: 4.3 README + 4.1 repo rename.
- **Next action**: none required. The split is live and self-sustaining.

---

## Phase 0 — Supabase RLS isolation (design + analysis only, ~no DB changes)

Why this first: prove the legal/technical split is feasible before building any
code. Output of Phase 0 = a green-light or a re-scope decision for Phase 1+.

- [x] **0.1 Inventory queries** on the public-render code path.
  → 2026-05-12: 11 queries enumerated in `migration/queries_inventory.md`.
- [x] **0.2 Categorize each query** as PUBLIC-safe or PRIVATE-required.
  → 9 PUBLIC, 2 PRIVATE (Q7=articles full-content, Q8=podcast refined).
  - **Rule**: PUBLIC = derives only from public sources (yfinance, Fed, TWSE)
    OR is Gemini-derived analysis (refined_content, raw_response,
    market_notes_json). PRIVATE = raw subscription-article content / podcast
    transcripts / anything sourced via login-required crawler.
  - **Output**: same file annotated.
- [x] **0.3 Draft RLS-equivalent restriction** — see `migration/rls_design.md`.
  → Picked **Option A1**: new Edge Function `db-proxy-public` with
    9-pattern hard allowlist. Avoids Postgres-direct dependency (port
    5432 blocked on company WiFi).
- [x] **0.4 Identify code paths that will break under restriction.**
  → 2 files / 2 queries (lines 942, 985 in generate_html.py).
    Both become DELETE actions in Phase 3.6, not refactors.
- [x] **0.5 Decide: apply restrictions now, or defer to Phase 3?**
  → **Defer to Phase 3.1**. Applying now blocks the current single-repo
    site immediately. Phase 0 outputs design + audit trail only.
- [ ] **0.6 Commit Phase 0 artifacts.**
  - `git add migration/ && git commit -m "migration(0): design complete"`

**Rollback**: Phase 0 produces only docs. No rollback needed.

---

## Phase 1 — Build new private repo (parallel, no cutover)

Goal: stand up `iia-ingest` with full copy of code; verify it can run, but
keep all its launchd jobs disabled.

- [x] **1.1 Confirm name** — `StockGG-ingest`. Local path:
      `~/Desktop/StockGG-ingest`. Admin UI: FastAPI + HTMX. Remote
      access: **Tailscale** (switched from Cloudflare Tunnel on 2026-05-13
      because user has no Cloudflare zones). Git history: full mirror.
- [x] **1.2 Create private GitHub repo** `GarlicChives/StockGG-ingest`.
      → https://github.com/GarlicChives/StockGG-ingest
- [x] **1.3 Clone + push to new remote** (`~/Desktop/StockGG-ingest`).
- [x] **1.4 Launchd plists updated** in new repo: all 8 plists repointed
      to `/Users/edward.song/Desktop/StockGG-ingest` + `Disabled: true`.
- [x] **1.5 Local smoke test** — DB connection works, 565 articles
      visible from new repo dir.
- [x] **1.6 Admin UI scaffold** — FastAPI + HTMX at
      `admin/`. `uv sync` brings in fastapi/uvicorn/jinja2.
      Verified `/healthz` + `/` render correctly on localhost:8765.
- [x] **1.7 Remote access decision** — **Localhost-only**.
      Company blocks Tailscale install. Cloudflare Tunnel rejected
      (needs domain + monthly maintenance for a use case the user can
      cover by opening Safari on this Mac). Admin UI binds to
      `127.0.0.1:8765`. Open `http://localhost:8765` from a browser on
      this Mac when needed. Cloudflare Tunnel + cheap domain remains
      the documented escape hatch if remote access becomes a real need.
- [x] **1.8 Commit baseline** in new repo — `1c6efc6` on
      `GarlicChives/StockGG-ingest:main`.

**Rollback**: delete new repo. Current repo untouched throughout.

---

## Phase 2 — Cutover scheduled jobs to new repo

- [ ] **2.1 Backup** current `launchctl list` output to
      `migration/launchctl_pre_cutover.txt`.
- [x] **2.2 Stop current repo's launchd jobs** — all 8 bootout OK.
      `com.iia.chrome-debug` (PID 1377) deliberately left running — it's
      the Playwright CDP Chrome instance, used by both old and new repo.
- [x] **2.3 Enable + bootstrap plists in new repo** — 8 plists bootstrap'd
      from `~/Desktop/StockGG-ingest/launchd/`. Disabled flag stripped,
      paths confirmed via `launchctl print`. `catchup` fired on RunAtLoad.
- [ ] **2.4 Watch 24-48 hours**. Monitor:
      - `~/Desktop/StockGG-ingest/logs/*.log` (NEW location, not old)
      - DB row counts in `articles`, `trading_rankings` continue to grow
      - Public site (https://stockgg.v4578469.workers.dev) keeps
        refreshing on next 07:30 CI run.
- [ ] **2.5 Rollback (only if 2.4 fails)**: see PROGRESS.md rollback
      block below.

**Rollback command (paste verbatim)**:
```
cd <current-repo>/launchd
for p in com.iia.{podcast-crawl,article-crawl,podcast-backfill,market-notes,tw-rankings,us-rankings,daily-briefing,catchup}; do
  launchctl bootout gui/$(id -u)/$p 2>/dev/null
  launchctl bootstrap gui/$(id -u) $PWD/$p.plist
done
```

---

## Phase 3 — Strip current repo, switch to public role

- [x] **3.1 db-proxy-public deployed** — supabase/functions/db-proxy-public/
      index.ts deployed to project mnseyguxiiditaybpfup. 9-pattern
      whitespace-normalized SQL allowlist; anything else → HTTP 403.
- [x] **3.2 SUPABASE_ANON_KEY** added to public repo .env and GitHub
      Secrets. SUPABASE_SERVICE_ROLE_KEY removed from public repo's
      secret list (private repo still has it for ingestion).
- [x] **3.3 src/utils/db.py** switched: EDGE_URL → db-proxy-public,
      reads SUPABASE_ANON_KEY (errors out if missing). service_role
      key reference deleted from this repo.
- [x] **3.4 Round-trip verified** — local generate_html.py produced a
      125 KB HTML; all 9 queries cleared the allowlist; CI run with
      anon-only credentials succeeded (see migration commits below).
- [x] **3.5 `git rm` modules** that no longer run in public repo —
      crawlers/, news/, theme/, prompts/, daily_report/market_notes/
      earnings_preview, refine, api_logger, browser, all scripts except
      generate_html.py, launchd/, PROMPTS.md. Kept: focus_themes.py,
      db.py, data/theme_dictionary.json, data/theme_rules.md.
- [x] **3.6 Removed PRIVATE HTML sections** from generate_html.py:
      Q7 (articles fetch) + Q8 (podcast refined) deleted; 「🎙 Podcast
      筆記」block deleted; modal 內的「📰 相關文章」section deleted;
      `sc-arts-hint` and per-card article counts deleted. Theme
      clustering now runs on volume-only signal (article-keyword score
      drops to 0). Public HTML shrunk from ~260 KB to ~130 KB.
- [x] **3.7 Simplified `market_briefing.yml`** to: checkout → setup uv →
      generate HTML → commit HTML → wrangler deploy. Dropped the
      daily_briefing + analysis Gemini steps. Added two extra cron
      schedules (18:15 / 23:15 TW) plus `repository_dispatch` trigger
      so the private repo can webhook-push after each analysis cycle.
- [x] **3.8 Webhook trigger from private repo** — done in
      StockGG-ingest `91022f4` (later `000cd6b` retargeted at renamed
      public repo). publish_trigger.py uses `gh workflow run` (no PAT
      needed; relies on local gh CLI auth on the Mac). daily_briefing
      Step 9 + end of run_market_notes both call it. Cron schedules
      remain as fallback.
- [x] **3.9 Removed launchd plists** from public repo (entire
      `launchd/` directory `git rm`'d in 3.5).
- [x] **3.10 Slimmed pyproject.toml** — dropped asyncpg, tavily-python,
      opencc-python-reimplemented, and the entire `[local]` extras
      group. Public repo now has 4 deps total. Renamed package to
      "stockgg" v0.2.0.

**Rollback**: `git revert` the Phase 3 commits. Re-enable bootout'd plists.

---

## Phase 4 — Public polish (optional, do after Phase 3 stable for 1 week)

- [x] **4.1 Renamed public repo** — Stock-test → stockgg (commit
      457c803e). GitHub redirect preserves the old URL.
- [x] **4.2 LICENSE (MIT) + 投資免責聲明** — page-footer disclaimer
      (3 paragraphs) + LICENSE additional notice. Commit 7d0414ca.
- [x] **4.3 README rewrite** — public-facing, describes site +
      architecture + isolation guarantee. .env.example slimmed.
      ARCHITECTURE.md removed (stale). Commit 7d0414ca.
- [ ] **4.4 Custom domain** — optional, defer until needed.

---

## Artifacts (files this migration produces)

- `migration/PROGRESS.md` — this file (master tracker)
- `migration/queries_inventory.md` — Phase 0.1 output
- `migration/rls_design.md` — Phase 0.3 output
- `migration/launchctl_pre_cutover.txt` — Phase 2.1 backup
- `migration/phase0_findings.md` — Phase 0.4-0.5 summary

---

## Open blockers

(none)

---

## Notes

- Memory pointer to this file is stored in
  `~/.claude/projects/-Users-edward-song-Desktop-Stock/memory/`.
- Each completed step → its own commit. `git log -- migration/` is the audit trail.
