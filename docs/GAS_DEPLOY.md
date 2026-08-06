# GAS デプロイ（clasp）

Apps Script のコードを、エディタへの手コピペなしに **ローカルの `gas/` から反映**する。

## しくみ

- この環境の node は Windows 版。clasp のシェル/CMD シムは WSL の bash から動かないため、
  `node.exe` で clasp 本体を直接実行するラッパー **`scripts/clasp.sh`** を用意している。
- clasp プロジェクトのルートは **`gas/`**（`.clasp.json` / `appsscript.json` を配置）。
- GAS は 土曜運用SS にバインドされたコンテナスクリプト（Script ID は `.clasp.json`）。

## 日常の使い方

```bash
# コード反映（HEAD）だけ。/dev(HEAD) デプロイURLには即反映される
./scripts/clasp.sh push

# 本番 /exec に反映（push + 既存デプロイのバージョン更新をまとめて実行）
./scripts/gas-deploy.sh "変更内容メモ"
```

- `./scripts/clasp.sh status` … push 対象/除外ファイルの確認
- `./scripts/clasp.sh pull`   … 本番から取得（※ ローカルを上書きするので通常は使わない）
- 初回のみ `./scripts/clasp.sh login`（ブラウザ認証）が必要。

> **なぜ deploy が要るか**: `clasp push` は HEAD を更新するだけ。本番 `/exec` URL は
> 固定バージョンを指すため、`clasp deploy -i <本番deploymentId>` で既存デプロイを
> 最新版に更新して初めて `/exec` に反映される（URL は変わらない）。
> `gas-deploy.sh` の `DEPLOY_ID` は secrets の `gas_webapp_url` と一致していること。

## 重要な注意

### clasp push は「プロジェクト全体の置換」
push は `gas/` の追跡ファイル一式で Apps Script を**丸ごと置き換える**。
ローカルに無いファイルは**本番から削除**される。編集は必ずローカル `gas/` を正とすること。

### 設定値の分離（config.gs）
環境依存の設定（`MASTER_SPREADSHEET_ID` / `ADMIN_EMAIL` / `TEST_MODE`）は
**`gas/config.gs`** に分離している。

- `gas/config.gs` … 実値。**git 管理外**（`.gitignore`）だが clasp では push される（＝本番設定を保持）。
- `gas/config.gs.example` … プレースホルダ。コミット対象。新規環境は
  `cp gas/config.gs.example gas/config.gs` して値を設定する。

GAS は全 `.gs` が同一名前空間を共有するため、`config.gs` のグローバル変数は
`common.gs` 等から参照できる。

### push 除外（.claspignore）
`gas/.claspignore` で `archive/**`・`*.md`・`*.sh` を除外している。
`gas/archive/reminder.gs`（旧コード）は**絶対に push しない**。
