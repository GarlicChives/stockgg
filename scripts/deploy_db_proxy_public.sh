#!/usr/bin/env bash
# Deploy db-proxy-public Edge Function with an auth liveness probe.
#
# 用這支腳本而不是直接 `supabase functions deploy`,目的是在 token 失效時
# 給出清楚、可行動的繁中提示。Supabase PAT 不會自然過期,但會因為帳號
# 密碼/2FA 變更、手動撤銷、macOS Keychain 變動等情況失效,而 CLI 的原始
# 錯誤 "Access token not provided" 不會明說該重新 login。
set -euo pipefail

PROJECT_REF="mnseyguxiiditaybpfup"
FUNCTION="db-proxy-public"

# Run from repo root so supabase/functions/ resolves correctly.
cd "$(dirname "$0")/.."

# Liveness probe -- same auth path as deploy, fails fast.
if ! supabase projects list >/dev/null 2>&1; then
  cat >&2 <<'EOF'

[!] Supabase CLI 未登入(或 token 已失效)。

請執行:
  supabase login

完成後再重跑本腳本。

token 不會自然過期,常見失效原因:
  - 在 dashboard.supabase.com/account/tokens 手動撤銷
  - 帳號密碼 / 2FA 變更
  - macOS Keychain 變動 / 重灌系統

EOF
  exit 1
fi

echo "[ok] Supabase CLI 已登入"
echo "[->] Deploying ${FUNCTION} to project ${PROJECT_REF}..."
supabase functions deploy "${FUNCTION}" --project-ref "${PROJECT_REF}"
echo "[ok] 部署完成"
