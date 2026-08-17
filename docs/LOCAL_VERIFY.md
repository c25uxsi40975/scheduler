# ローカル検証ガイド（読み取り専用モード）

git push → Streamlit Cloud デプロイ、という検証ループを避けて、**ローカルで実データを見ながら安全に検証**するための手順。

## しくみ：読み取り専用モード

環境変数 `SCHEDULER_READONLY=1` を設定すると、アプリは

- **読み取り**：実 Google Sheets から通常どおり取得（実データを表示）
- **書き込み**：スプレッドシートへの保存・シート作成・LINE/メール/Drive 通知を**すべて無効化**（no-op）

で動作する。本番データは一切変更されないので、保存ボタンを押しても安全。画面上部に `🔒 読み取り専用モード` のバナーが出る。

実装は [database/connection.py](../database/connection.py) の `_ReadOnlyWorksheet` プロキシ。worksheet 取得の3アクセサ（`_get_sheet` / `_get_weekday_sheet` / `_init_monthly_sheet`）と `init_db()` を1箇所で塞いでいる。デフォルト（環境変数なし）では従来どおり読み書きするため、**本番挙動は不変**。

## 1回だけの準備

```bash
# 依存インストール（.venv は作成済み。無ければ python3 -m venv .venv）
.venv/bin/pip install -r requirements.txt

# secrets を配置（Streamlit Cloud の Settings → Secrets の内容をコピー）
#   .streamlit/secrets.toml
# ※ このファイルは秘密情報。リポジトリにコミットしないこと（.gitignore 済みを確認）
```

## 使い方

### A. ブラウザで目視確認（hot reload）

```bash
./scripts/run_local_readonly.sh
```

`http://localhost:8501` が開く。ファイルを保存すると自動で再実行される（右上「Always rerun」を有効化）。**push 不要**。保存操作をしても実データは変わらない。

### B. ヘッドレス・スモークテスト（クラッシュ検知）

secrets 配置後、全管理者ページが例外なく描画されるかを一括チェック：

```bash
.venv/bin/python tests/test_smoke_pages.py
```

`.streamlit/secrets.toml` が無ければ自動スキップする。リファクタ後に「どこかのページが壊れていないか」を素早く保証できる。

### C. 読み取り専用ガードの単体テスト（secrets 不要）

```bash
.venv/bin/python tests/test_readonly_guard.py
```

プロキシが「読み取りは通す／書き込みは全ブロック」する挙動を、ネットワーク・secrets なしで検証する。

### D. 日程希望 代理入力の保存ロジック（secrets 不要）

```bash
.venv/bin/python tests/test_pref_matrix.py
```

代理入力マトリクスで「-」（未入力）が ○（可能）として保存されないこと、
全て「-」に戻すと未入力へ戻ることを、スタブDBで検証する。

## 本番（Streamlit Cloud）への影響

環境変数 `SCHEDULER_READONLY` を設定しなければ従来どおり。Cloud 側では設定しないこと（設定すると保存できなくなる）。
