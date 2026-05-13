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

- **Current phase**: Phase 1 complete; ready for Phase 2 cutover
- **Last completed step**: 1.7 — localhost-only, no remote access infra
- **Next action**: 2.1 — backup current launchctl state to
  `migration/launchctl_pre_cutover.txt` (read-only, zero risk)

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
- [ ] **2.2 Stop current repo's launchd jobs**:
      `launchctl bootout gui/$(id -u)/com.iia.<name>` for each:
      podcast-crawl, article-crawl, podcast-backfill, market-notes,
      tw-rankings, us-rankings, daily-briefing, catchup, chrome-debug.
- [ ] **2.3 Enable plists in new repo** (remove Disabled flag), then
      `launchctl bootstrap gui/$(id -u) <new-repo-path>/launchd/<file>.plist`
      for each.
- [ ] **2.4 Watch 24-48 hours**. Monitor:
      - `logs/*.log` in new repo
      - DB row counts in `articles`, `trading_rankings` continue to grow
      - Public site keeps refreshing daily
- [ ] **2.5 If any job fails**: rollback by reversing 2.3 and 2.2.

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

- [ ] **3.1 Apply RLS / deploy `db-proxy-public`** from Phase 0 design.
- [ ] **3.2 Add `SUPABASE_PUBLIC_KEY`** to current repo's GitHub Secrets
      + local `.env`.
- [ ] **3.3 Modify `src/utils/db.py`** to read the public key in this repo
      (or branch path).
- [ ] **3.4 Run full CI/local build with restricted credentials**; expect
      failures. Fix each — usually by deleting the offending query.
- [ ] **3.5 `git rm` modules** that no longer run in this repo:
      `src/crawlers/`, `src/news/{market_data,tw_rankings,us_rankings,
      catalyst_calendar}.py`, `src/analysis/{daily_report,market_notes,
      earnings_preview}.py`, `src/utils/refine.py`, `src/theme/`,
      `scripts/{daily_briefing,run_market_notes,build_theme_dictionary,
      podcast_backfill,transcribe_one,manage_watchlist,
      fetch_rankings,catchup,...}.py`.
- [ ] **3.6 Remove legally-risky HTML sections** from generate_html.py:
      Podcast notes tab content, article modal 內的「相關文章」block,
      possibly 跨來源議題 article-title quotes.
- [ ] **3.7 Simplify market_briefing.yml CI** to:
      checkout → setup uv → run generate_html → wrangler deploy. Drop
      the daily_briefing step entirely.
- [ ] **3.8 New webhook in private repo**: at end of run_market_notes.py
      (private), call `gh workflow run market_briefing.yml --repo
      <public-repo>`.
- [ ] **3.9 Remove launchd plists** from current repo (already disabled
      in Phase 2, now delete files).
- [ ] **3.10 Final commit**: "migration(3): public repo stripped".

**Rollback**: `git revert` the Phase 3 commits. Re-enable bootout'd plists.

---

## Phase 4 — Public polish (optional, do after Phase 3 stable for 1 week)

- [ ] **4.1 Rename public repo** on GitHub.
- [ ] **4.2 Add LICENSE + 投資免責聲明**.
- [ ] **4.3 README rewrite** for public audience.
- [ ] **4.4 Custom domain on Cloudflare** (if desired).

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
