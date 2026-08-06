#!/usr/bin/env bash
# WSL(bash) から Windows 版 Node で clasp を実行するラッパー。
#
# 事情: この環境の node/npm は Windows 版（hermes）。clasp の実行シム
#   （clasp.cmd / clasp.ps1）は WSL の bash からは動かず、PowerShell の
#   ExecutionPolicy 回避もブロックされる。そこで clasp の JS 本体を
#   node.exe で直接実行する（.exe は WSL interop で素直に動く）。
#
# 使い方（リポジトリのどこからでも）:
#   ./scripts/clasp.sh login          # 初回ログイン（ブラウザ認証）
#   ./scripts/clasp.sh status         # 追跡ファイル確認
#   ./scripts/clasp.sh push           # gas/ を Apps Script へ反映
#   ./scripts/clasp.sh pull           # Apps Script から取得
set -euo pipefail

# clasp 本体（グローバル install 先。node prefix は npm config get prefix で確認可能）
CLASP_JS='C:\Users\hmats\AppData\Local\hermes\node\node_modules\@google\clasp\build\src\index.js'

# clasp プロジェクトのルート = gas/（.clasp.json / appsscript.json / *.gs を配置）
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR/../gas"

exec node.exe "$CLASP_JS" "$@"
