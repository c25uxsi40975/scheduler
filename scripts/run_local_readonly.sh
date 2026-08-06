#!/usr/bin/env bash
# 読み取り専用モードでローカル起動する。
# 実データを表示するが、保存・シート作成・LINE/メール通知は一切行わない（検証用）。
#
# 前提: .venv 構築済み、.streamlit/secrets.toml 配置済み。
# 使い方: ./scripts/run_local_readonly.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export SCHEDULER_READONLY=1
exec .venv/bin/streamlit run app.py "$@"
