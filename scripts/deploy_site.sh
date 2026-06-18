#!/usr/bin/env bash
# Deploy guard for the public site.
#
# 根因(2026-06-18):docs/index.html / history.json / kline.json 是 gitignored
# 生成檔。若在「只有 committed 的 app.js+style.css、尚未跑 generate_html.py」的
# 狀態下裸跑 `wrangler deploy`,會上傳一個「缺 index.html」的版本 → production
# alias 一旦切過去就全站根 404(SPA fallback 也救不了,因為 index.html 根本沒上傳)。
#
# 本腳本是唯一該用的部署入口:部署前斷言 index.html 完整,部署後 smoke-test
# 線上根路徑必須 200,否則非零退出。CI 與本機都走這支,杜絕空版本變 active。
set -euo pipefail

cd "$(dirname "$0")/.."

INDEX="docs/index.html"
MIN_BYTES=500000          # 真實檔 ~2.8MB;500KB floor 擋掉「缺檔/截斷」
PROD_URL="https://stockgg.v4578469.workers.dev/"

echo "▶ pre-deploy 檢查 ..."

# 1) index.html 必須存在
if [[ ! -f "$INDEX" ]]; then
  echo "✗ $INDEX 不存在 —— 先跑 'uv run scripts/generate_html.py' 再部署。" >&2
  exit 1
fi

# 2) 大小護欄(缺檔=0、截斷=過小)
bytes=$(wc -c < "$INDEX" | tr -d ' ')
if (( bytes < MIN_BYTES )); then
  echo "✗ $INDEX 只有 ${bytes} bytes(< ${MIN_BYTES})—— 疑似生成失敗/截斷,拒絕部署。" >&2
  exit 1
fi

# 3) conflict marker 護欄
if grep -lq "<<<<<<<" docs/index.html docs/app.js docs/style.css 2>/dev/null; then
  echo "✗ 偵測到 git conflict marker —— 拒絕部署。" >&2
  exit 1
fi

# 4) 結構 sanity:結尾要是 </html>
if ! tail -c 64 "$INDEX" | grep -q "</html>"; then
  echo "✗ $INDEX 結尾不是 </html> —— 疑似截斷,拒絕部署。" >&2
  exit 1
fi

echo "✓ index.html ${bytes} bytes,結構完整。"
echo "▶ wrangler deploy ..."
# 捕捉部署輸出以取出版本 ID。tee 讓你仍看得到即時輸出。
deploy_out=$(npx wrangler deploy 2>&1 | tee /dev/tty)

# 5) post-deploy smoke test。
#
# 站台前面有 Cloudflare Access:production root 對未登入請求回 302(登入頁),
# 不是 200,所以「測 production root == 200」在 Access 下永遠失敗。改測**版本
# 預覽 URL**(https://<versionId前8碼>-stockgg.v4578469.workers.dev/):它繞過
# Access,又直接命中剛部署那個版本的 assets —— 200 才證明「index.html 真的上傳了、
# 這個版本不是空版本」,正是我們要防的那個 bug。
ver=$(printf '%s\n' "$deploy_out" | grep -oE 'Current Version ID: [0-9a-fA-F-]+' | head -1 | awk '{print $4}')

if [[ -n "$ver" ]]; then
  preview="https://${ver:0:8}-stockgg.v4578469.workers.dev/"
  echo "▶ post-deploy smoke test(版本預覽,繞過 Access)${preview} ..."
  for attempt in 1 2 3 4 5; do
    sleep 3
    code=$(curl -sS -o /dev/null -w "%{http_code}" "$preview" || echo "000")
    echo "  attempt ${attempt}: HTTP ${code}"
    if [[ "$code" == "200" ]]; then
      echo "✓ 版本 ${ver:0:8} 根路徑 200 —— assets 完整、部署成功。"
      exit 0
    fi
  done
  echo "✗ 版本 ${ver:0:8} 根路徑非 200 —— 此版本可能缺 index.html,請排查後再部署。" >&2
  exit 1
fi

# 取不到版本 ID(wrangler 輸出格式變動)→ 退回測 production root,接受 200 或
# 302(302 = Access 攔截,代表 worker 活著)。
echo "⚠ 取不到版本 ID,退回測 ${PROD_URL}(接受 200 或 302)..."
for attempt in 1 2 3 4 5; do
  sleep 3
  code=$(curl -sS -o /dev/null -w "%{http_code}" "$PROD_URL" || echo "000")
  echo "  attempt ${attempt}: HTTP ${code}"
  if [[ "$code" == "200" || "$code" == "302" ]]; then
    echo "✓ 線上有回應(HTTP ${code})—— 部署完成。"
    exit 0
  fi
done

echo "✗ 部署後線上無正常回應 —— 請用版本預覽 URL 排查。" >&2
exit 1
