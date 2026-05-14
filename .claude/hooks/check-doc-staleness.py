#!/usr/bin/env python3
"""PostToolUse hook — print a stderr reminder when Claude edits a
"structural" file. Stderr from a Claude Code hook gets injected back into
the model's context, so this is enforced by the harness, not by Claude's
judgment.

Hook installed via .claude/settings.json `hooks.PostToolUse` with matcher
"Edit|Write|MultiEdit".

Triggers on any path matching the patterns below (covers both repos with
a single union list — non-applicable patterns just won't match in the
other repo).
"""
import json
import re
import sys

# Files that imply a "structural" change requiring SYSTEM.md / CLAUDE.md sync
STRUCTURAL_PATTERNS = [
    r"launchd/com\.iia\..*\.plist$",           # any scheduling change
    r"src/utils/db\.py$",                       # DB layer / auth
    r"src/utils/publish_trigger\.py$",          # webhook target
    r"\.github/workflows/.*\.ya?ml$",           # CI config
    r"supabase/functions/db-proxy-public/.*",   # public SQL allowlist
    r"pyproject\.toml$",                        # deps
    r"scripts/(daily_briefing|run_market_notes)\.py$",  # main orchestrators
    r"src/crawlers/.*\.py$",                    # new/changed crawler
    r"src/prompts/.*\.md$",                     # prompt content
    r"\.env\.example$",                         # env structure
    r"wrangler\.jsonc$",                        # CF Workers config
]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        return 0
    for pat in STRUCTURAL_PATTERNS:
        if re.search(pat, file_path):
            sys.stderr.write(
                "\n[doc-guard] ✋  Structural file edited:\n"
                f"  {file_path}\n"
                "  ─ Re-check `~/Desktop/StockGG-ingest/SYSTEM.md` 「異動觸發表」 row to\n"
                "    see which doc must be updated in the same commit.\n"
                "  ─ Pre-commit hook will reject if you forget.\n\n"
            )
            break
    return 0  # never block tool execution


if __name__ == "__main__":
    sys.exit(main())
