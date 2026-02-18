# セットアップガイド（管理者向け）

デプロイに必要な手動作業の一覧です。

---

## 1. GCP プロジェクト作成

1. https://console.cloud.google.com/ にアクセス（Googleアカウントでログイン）
2. 上部の「プロジェクトを選択」→「新しいプロジェクト」
3. プロジェクト名: `gaikin-scheduler`（任意）で作成

## 2. API の有効化

1. 左メニュー「APIとサービス」→「ライブラリ」
2. **Google Sheets API** を検索 →「有効にする」
3. **Google Drive API** を検索 →「有効にする」

## 3. サービスアカウント作成

1. 「APIとサービス」→「認証情報」→「認証情報を作成」→「サービスアカウント」
2. サービスアカウント名: `gaikin-app`（任意）で作成
3. 作成後、サービスアカウントのメールアドレスを控える
   - 例: `gaikin-app@gaikin-scheduler.iam.gserviceaccount.com`

## 4. JSON キーの発行

1. 作成したサービスアカウントをクリック
2. 「鍵」タブ →「鍵を追加」→「新しい鍵を作成」→ **JSON** を選択
3. JSONファイルがダウンロードされる
4. **このファイルは厳重に管理する**（GitHubにアップロードしない）

## 5. Google スプレッドシートの作成（2ファイル）

マスタデータ（医員・外勤先等）と運用データ（希望・スケジュール）を分けて管理します。

### 5-1. マスタ用スプレッドシート

1. Google ドライブで新規スプレッドシートを作成
2. ファイル名を `外勤調整_マスタ`（任意）に変更
3. 共有設定で、手順3で控えたサービスアカウントのメールアドレスを **「編集者」** として追加
4. URLからスプレッドシートキーを控える

### 5-2. 運用データ用スプレッドシート

1. Google ドライブでもう1つ新規スプレッドシートを作成
2. ファイル名を `外勤調整_運用データ`（任意）に変更
3. 同様にサービスアカウントを **「編集者」** として共有
4. URLからスプレッドシートキーを控える

> **スプレッドシートキーの取得方法:**
> ブラウザでスプレッドシートを開き、URLバーの `https://docs.google.com/spreadsheets/d/【ここがキー】/edit` の `/d/` と `/edit` の間の文字列です。

※ シート（タブ）はアプリが自動作成するので、手動で作る必要はありません。

> **既存環境からの移行:** 1つのスプレッドシートで運用していた場合、運用データ用スプレッドシートを新規作成し、`希望_YYYY-MM` / `スケジュール_YYYY-MM` シートを「別のスプレッドシートにコピー」で移行してください。

## 6. GitHubリポジトリをpublicにする

1. GitHubの `hmatsu88yama/scheduler` リポジトリへ移動
2. Settings → General → Danger Zone → Change visibility → **Public** に変更

※ コードにパスワードや個人情報は含まれていません（`.gitignore` で除外済み）。

## 7. Streamlit Cloud でデプロイ

1. https://share.streamlit.io にアクセス
2. GitHubアカウントでサインイン
3. 「New app」→ 以下を設定:
   - **Repository**: `hmatsu88yama/scheduler`
   - **Branch**: `main`
   - **Main file path**: `app.py`
4. デプロイ前に「Advanced settings」→「Secrets」に以下を貼り付け:

```toml
[gcp_service_account]
type = "service_account"
project_id = "（JSONキーの project_id）"
private_key_id = "（JSONキーの private_key_id）"
private_key = "（JSONキーの private_key ※改行は \\n で1行にする）"
client_email = "（JSONキーの client_email）"
client_id = "（JSONキーの client_id）"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "（JSONキーの client_x509_cert_url）"

spreadsheet_key = "（マスタ用スプレッドシートキー）"
spreadsheet_key_operational = "（運用データ用スプレッドシートキー）"
```

> **JSONキーの貼り付け方:**
> - JSONキーファイルの各値をそのまま貼り付けてください
> - `private_key` の値は `"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"` の形式で、改行を `\n` に変換して1行にします
>
> **スプレッドシートキー** は手順5で控えた値を使用してください。

5. 「Deploy」をクリック

## 8. デプロイ後の初期設定

1. デプロイされたアプリのURLにアクセス
2. 「管理者としてログイン」→ 管理者パスワードを設定
3. 「マスタ管理」→ 医員用パスワードを設定
4. 医員・外勤先を登録
5. 医員にURLと医員用パスワードを共有

---

## 9. メールリマインダー・確定通知（任意）

毎週金曜18時のリマインダーと、スケジュール確定時の通知メールを設定できます。
詳細な手順は [gas/SETUP.md](gas/SETUP.md) を参照してください。

### 9-1. 概要

| 機能 | タイミング | 送信対象 |
|------|-----------|---------|
| リマインダー | 毎週金曜18時 | 翌日外勤がある医員のみ |
| 確定通知 | 管理者がスケジュール確定時 | メールアドレス登録済みの全医員 |

### 9-2. 必要な作業

1. 管理画面で各医員のメールアドレスを登録
2. 運用データ用スプレッドシートで「拡張機能」→「Apps Script」を開く
3. `gas/reminder.gs` の内容を貼り付けて保存
4. スクリプト内の `MASTER_SPREADSHEET_ID` にマスタ用スプレッドシートのキーを設定
5. リマインダー用のトリガーを設定（毎週金曜18時）
6. Web Appとしてデプロイし、URLをStreamlit CloudのSecretsに追加:

```toml
gas_webapp_url = "https://script.google.com/macros/s/.../exec"
```

---

## 確認チェックリスト

- [ ] GCP プロジェクト作成済み
- [ ] Google Sheets API 有効化済み
- [ ] Google Drive API 有効化済み
- [ ] サービスアカウント作成済み
- [ ] JSON キー発行済み
- [ ] マスタ用スプレッドシート作成済み
- [ ] 運用データ用スプレッドシート作成済み
- [ ] 両スプレッドシートをサービスアカウントに共有済み
- [ ] GitHub リポジトリを public に変更済み
- [ ] Streamlit Cloud の Secrets に JSON キー・両スプレッドシートキー登録済み
- [ ] デプロイ完了、アプリにアクセスできる
- [ ] 管理者パスワード設定済み
- [ ] 医員用パスワード設定済み
- [ ] マスタ用スプレッドシートにマスタシートが自動作成されている

**メール通知（任意）:**
- [ ] 医員のメールアドレス登録済み
- [ ] Google Apps Scriptにコード貼り付け済み
- [ ] GASの権限承認済み
- [ ] リマインダートリガー設定済み（毎週金曜18時）
- [ ] Web Appデプロイ済み
- [ ] Secrets に `gas_webapp_url` 追加済み

---

## 注意事項

- 両スプレッドシートの共有は**サービスアカウントのみ**に限定してください
- 「リンクを知っている全員」に設定しないこと
- JSONキーファイルをGitHubにアップロードしないこと
- `admin_password.txt` はローカルでのメモ用です（コミットされません）
