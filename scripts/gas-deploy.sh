#!/usr/bin/env bash
# GAS をローカルから本番へワンコマンド反映する。
#
#   1) clasp push -f    … gas/ のコードを Apps Script(HEAD) に反映
#   2) clasp deploy -i  … 本番デプロイ（/exec URL）を最新版へ更新
#
# 本番 /exec の deploymentId（= gas_webapp_url の /macros/s/●●●/exec の●●●部分）は
# git 管理外にする（gas_webapp_url と同様に秘匿寄りのため）。
#   - 環境変数 GAS_DEPLOY_ID、または
#   - scripts/.gas_deploy_id ファイル（scripts/.gas_deploy_id.example 参照）
# から読み込む。
set -euo pipefail
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

DEPLOY_ID="${GAS_DEPLOY_ID:-$(cat "$DIR/.gas_deploy_id" 2>/dev/null || true)}"
if [ -z "${DEPLOY_ID:-}" ]; then
  echo "本番 deploymentId が未設定です。" >&2
  echo "  scripts/.gas_deploy_id を作成（scripts/.gas_deploy_id.example を参照）" >&2
  echo "  または環境変数 GAS_DEPLOY_ID を設定してください。" >&2
  exit 1
fi

DESC="${1:-manual deploy}"

echo "== clasp push =="
"$DIR/clasp.sh" push -f

echo "== clasp deploy (本番 /exec を最新版へ) =="
"$DIR/clasp.sh" deploy -i "$DEPLOY_ID" -d "$DESC"

echo "== 完了。/exec に反映されました =="
