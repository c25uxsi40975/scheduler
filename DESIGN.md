# 外勤調整システム - 設計書

## システム概要

病院の医員（医師）を土曜日の外勤先に割り当てるスケジューリングシステム。
管理者が外勤先・医員を管理し、医員が希望を入力、PuLP（線形計画法）で最適な割り当てを自動生成する。

- **フレームワーク:** Streamlit
- **ホスティング:** Streamlit Cloud
- **DB:** SQLite（`gaikin.db`）※ Google Sheets移行予定（後述）
- **最適化:** PuLP（CBC Solver、タイムリミット30秒）
- **祝日判定:** jpholiday

---

## ファイル構成

```
scheduler/
├── app.py                          メインエントリポイント（ログイン・ルーティング）
├── database.py                     データベース層
├── optimizer.py                    スケジュール最適化エンジン
├── components/
│   ├── __init__.py
│   └── schedule_table.py          共通テーブル表示
├── pages/
│   ├── __init__.py
│   ├── admin_master.py            マスタ管理
│   ├── admin_preferences.py       希望状況一覧
│   ├── admin_generate.py          スケジュール生成
│   ├── admin_schedule.py          確定スケジュール確認
│   ├── doctor_input.py            医員希望入力
│   └── doctor_schedule.py         医員スケジュール確認
├── seed_data.py                   テスト用データ投入
├── requirements.txt
└── README.md
```

---

## ファイル間の依存関係

```
app.py
├── database.py        (init_db, get_doctors, delete_old_schedules, auth関数)
├── optimizer.py       (get_target_saturdays)
└── pages/
    ├── admin_master.py       → database.py
    ├── admin_preferences.py  → database.py, optimizer.py
    ├── admin_generate.py     → database.py, optimizer.py, components/
    ├── admin_schedule.py     → database.py, components/
    ├── doctor_input.py       → database.py, optimizer.py
    └── doctor_schedule.py    → database.py, components/
```

---

## 認証・画面遷移

### ログインフロー

```
ロール選択画面
├── 「管理者としてログイン」
│   ├── 初回: パスワード設定画面（確認入力付き）
│   └── 2回目以降: パスワード入力画面
│       └── 認証成功 → 管理者タブ画面
└── 「医員としてログイン」
    ├── パスワード入力画面（管理者が設定した共通パスワード）
    │   └── 認証成功 → 名前選択画面
    └── 名前選択画面
        └── 選択 → 医員タブ画面
```

### 認証方式

- **管理者**: 共通パスワード1つ（SHA-256ハッシュ化してDBの`settings`テーブルに保存）
- **医員**: 共通パスワード1つ（同上） + 名前選択
  - パスワードは管理者がマスタ管理画面で設定
  - 未設定時は医員ログイン不可（設定を促すメッセージ表示）
- セッション管理: `st.session_state` で `role`, `admin_authenticated`, `doctor_authenticated`, `doctor_id` を保持
- ログアウト: サイドバーのボタンでロール選択画面に戻る

---

## ユーザー種別とタブ構成

| ユーザー | タブ |
|---|---|
| 管理者 | マスタ管理 / 希望状況一覧 / スケジュール生成 / スケジュール確認 |
| 医員 | 希望入力 / スケジュール確認 |

---

## データベース設計

### テーブル一覧

| テーブル | 用途 | 主なカラム |
|---|---|---|
| `doctors` | 医員マスタ | id, name, is_active |
| `clinics` | 外勤先マスタ | id, name, fee(日当), frequency(頻度), preferred_doctors(指名), is_active |
| `preferences` | 医員の月次希望 | doctor_id, year_month, ng_dates(×日), avoid_dates(△日), preferred_clinics |
| `schedules` | 生成スケジュール | year_month, plan_name, assignments(JSON), total_variance, satisfaction_score, is_confirmed |
| `doctor_clinic_affinity` | 医員-外勤先の優先度 | doctor_id, clinic_id, weight(◎=2.0/○=1.0/×=0.0) |
| `clinic_date_overrides` | 外勤先の日別設定 | clinic_id, date, required_doctors(0=休診/1=通常/2=2人体制) |
| `settings` | アプリ設定 | key, value（管理者パスワードハッシュ等） |

### 外勤先の頻度区分

| frequency値 | 意味 |
|---|---|
| `weekly` | 毎週 |
| `biweekly_odd` | 隔週（奇数週） |
| `biweekly_even` | 隔週（偶数週） |
| `first_only` | 第1週のみ |
| `last_only` | 最終週のみ |

### 医員-外勤先 優先度（◎○×方式）

管理者がマスタ管理画面で、各医員と外勤先の組み合わせに対して設定する。

| 記号 | weight値 | 意味 | 最適化への反映 |
|---|---|---|---|
| ◎ | 2.0 | 月1回以上必ず行く | ハード制約（必ず1回以上割り当て） |
| ○ | 1.0 | 行くときもある | ソフト制約（割り当て候補、デフォルト） |
| × | 0.0 | まったく行かない | ハード制約（割り当て禁止） |

### 外勤先の日別設定（clinic_date_overrides）

管理者がマスタ管理画面で、特定の外勤先の特定日に対して設定する。

| required_doctors | 意味 | 最適化への反映 |
|---|---|---|
| 0 | 休診 | スロットを生成しない（割り当てなし） |
| 1 | 通常（デフォルト） | 1人割り当て |
| 2 | 2人体制 | 2人割り当て |

デフォルト（オーバーライドなし）は通常(1人)。`required_doctors=1` のレコードは保存せず削除する。

### 医員の日程希望（○△×方式）

医員が希望入力画面で、対象月の各土曜日に対して設定する。

| 記号 | 保存先 | 意味 | 最適化への反映 |
|---|---|---|---|
| ○ | (該当なし) | 出勤可能 | 制約なし |
| △ | avoid_dates | できれば避けたい | ソフトペナルティ（割り当て可だがコスト加算） |
| × | ng_dates | NG（出勤不可） | ハード制約（割り当て禁止） |

---

## 最適化エンジン設計

### 制約条件（ハード制約）

| # | 制約 | 内容 |
|---|---|---|
| 1 | スロット人数 | 各外勤先・各日に必要人数を割り当て（通常1人、2人体制なら2人、休診なら0=スロット除外） |
| 2 | 1日1外勤 | 各医員は同一日に最大1ヶ所 |
| 3 | ×日除外 | 医員がNG（×）指定した日には割り当てない |
| 4 | ×外勤先除外 | 優先度×の外勤先には割り当てない |
| 5 | ◎外勤先必須 | 優先度◎の外勤先には月1回以上割り当てる |

### 目的関数の構成要素（ソフト制約）

| 要素 | 内容 |
|---|---|
| `variance_term` | 報酬ばらつき最小化（各医員の報酬と平均の差の絶対値和） |
| `preference_term` | 医員の希望外勤先マッチ |
| `nomination_term` | 外勤先の指名医員マッチ |
| `priority_term` | 優先度スコア加算（◎=2, ○=1） |
| `avoid_penalty` | △日ペナルティ（できれば避けたい日に割り当てるとコスト加算） |
| `count_variance` | 外勤回数のばらつき最小化 |

### 3つの生成モード

| モード | 重視ポイント | 重み (var / pref / nom / pri / avoid / cnt) |
|---|---|---|
| `balanced` | 給与均等 | 10 / -1 / -2 / -1 / 3 / 5 |
| `preference` | 医員希望 | 2 / -5 / -3 / -2 / 3 / 3 |
| `affinity` | 優先度 | 2 / -2 / -2 / -5 / 3 / 3 |

---

## 各ファイルの機能詳細

### app.py - メインエントリポイント

- ページ設定（タイトル、レイアウト）
- DB初期化、古いデータの自動削除（4ヶ月保持）
- ロール選択画面（管理者 / 医員）
- 管理者パスワード認証（初回は設定画面、以降はログイン画面）
- 医員パスワード認証 + 名前選択画面
- 認証後：サイドバーに対象月選択・ログアウトボタン、メインにタブ表示

### database.py - データベース層

- SQLite接続管理（WALモード、外部キー有効）
- 7テーブルのCRUD操作
- JSON列の自動シリアライズ/デシリアライズ（ng_dates, avoid_dates, preferred_clinics, assignments等）
- 管理者・医員パスワード管理（SHA-256ハッシュ化、settingsテーブル）
- 既存DBへのマイグレーション対応（avoid_datesカラム追加）
- 古いデータの自動クリーンアップ

### optimizer.py - 最適化エンジン

- 対象土曜日の算出（祝日除外）
- 外勤先頻度に応じた対象日フィルタリング
- 日別オーバーライド対応（休診スロット除外、2人体制の人数可変制約）
- PuLPによる0-1整数計画問題の定式化・求解
- ◎必須のハード制約、×禁止のハード制約、△のソフトペナルティ
- 3モード×1回の一括プラン生成
- 結果の統計算出（報酬分散、満足度スコア）

### components/schedule_table.py - 共通コンポーネント

- スケジュールデータをカレンダー形式DataFrameに変換
- 行=外勤先、列=日付（MM/DD(曜日)）、セル=医員名
- 3画面（生成結果、確定確認、医員確認）で共用

### pages/admin_master.py - マスタ管理

- 医員用パスワードの設定（expanderで折りたたみ）
- 医員の追加・有効/無効切替・名前変更・削除
- 外勤先の追加・有効/無効切替・編集（日当・頻度の変更）
- 外勤先ごとの指名医員設定（multiselect）
- 医員-外勤先の優先度設定（◎○×ラジオボタン）
- 外勤先の日別設定（対象月の各日に通常/2人体制/休診を設定）

### pages/admin_preferences.py - 希望状況一覧

- 全医員の希望入力状況を一覧テーブル表示
- 各土曜の状況を○/△/×で表示
- 入力済人数のカウント表示

### pages/admin_generate.py - スケジュール生成

- 生成前のバリデーション（医員・外勤先・土曜日の存在確認）
- 過去の全確定スケジュールからの累計報酬自動計算（対象月より前の全月分）
- 3案一括生成 → DB保存
- 生成済み案のexpander表示（カレンダー + 医員別統計）
- 案の確定機能（同月1案のみ）
- 不要な案の削除機能（確認ダイアログ付き）
- 手動調整機能（各スロットの担当医員をselectboxで変更可能）

### pages/admin_schedule.py - 確定スケジュール確認

- 確定済みスケジュールのカレンダー表示
- CSVダウンロード（UTF-8 BOM付き）

### pages/doctor_input.py - 医員希望入力

- 日程の希望入力（各土曜に○/△/×をラジオボタンで選択）
- 希望外勤先選択（multiselect、外勤先名+日当表示）
- UPSERT保存（同月は上書き更新）

### pages/doctor_schedule.py - 医員スケジュール確認

- 自分の外勤予定をリスト表示（日付→外勤先）
- 全体スケジュールのカレンダー表示

### seed_data.py - テスト用データ投入

- 医員20人、外勤先10ヶ所のサンプルデータ登録
- ランダムな指名・優先度を設定（seed=42で再現可能）

---

## 未実装・改善候補一覧

### 認証・セキュリティ

- [ ] `update_clinic()` のSQLインジェクション対策（key部分が未検証）
- [ ] 管理者パスワードの変更UI

### 希望管理 (admin_preferences / doctor_input)

- [ ] 未入力医員への催促機能
- [ ] 希望外勤先の一覧表示（現在は日程のみ表示）
- [ ] 入力期限の表示・制御

### スケジュール確認 (admin_schedule / doctor_schedule)

- [ ] 確定の取消機能
- [ ] 印刷用レイアウト
- [ ] 医員スケジュール確認での自分の行ハイライト
- [ ] カレンダーアプリ連携（iCal出力等）

### その他

- [ ] 隔週判定の改善（月内インデックスベース → 実際の週番号ベース）

---

## Google Sheets 移行計画

### 背景

Streamlit Cloudではファイルシステムがエフェメラル（再起動でリセット）のため、SQLiteではデータが永続化できない。
Google Sheetsをデータベースとして使用することで、無料かつ複数ユーザーでのデータ共有を実現する。

### 目標構成

```
Google ドライブ
└── Google スプレッドシート（データベース）
    ├── 医員マスタ
    ├── 外勤先マスタ
    ├── 優先度マスタ（◎○×）
    ├── 日別設定（clinic_date_overrides）
    ├── 設定（パスワード等）
    ├── 希望_YYYY-MM（月ごと）
    └── スケジュール_YYYY-MM（月ごと）
         ↑ Google Sheets API（gspread）
         │
Streamlit Cloud アプリ（既存コード）
```

### 使用サービス

| サービス | 用途 | 費用 |
|---|---|---|
| Google スプレッドシート | データベース（保管・閲覧） | 無料 |
| GCP（Google Sheets API） | スプレッドシートへのAPI接続 | 無料枠内 |
| Streamlit Cloud | アプリのホスティング | 無料（1アプリ） |
| gspread | Python → スプレッドシート接続ライブラリ | OSS 無料 |

### シート構成

#### 「医員マスタ」
| 列 | 内容 | 例 |
|---|---|---|
| A: id | 連番 | 1 |
| B: name | 医員名 | 田中太郎 |
| C: is_active | 有効フラグ | TRUE |

#### 「外勤先マスタ」
| 列 | 内容 | 例 |
|---|---|---|
| A: id | 連番 | 1 |
| B: name | 外勤先名 | A総合病院 |
| C: fee | 日当（円） | 80000 |
| D: frequency | 頻度 | weekly |
| E: preferred_doctors | 指名医員ID（カンマ区切り） | 1,3,5 |
| F: is_active | 有効フラグ | TRUE |

#### 「優先度マスタ」（doctor_clinic_affinity）
| 列 | 内容 | 例 |
|---|---|---|
| A: doctor_id | 医員ID | 1 |
| B: clinic_id | 外勤先ID | 3 |
| C: weight | 優先度（◎=2.0 / ○=1.0 / ×=0.0） | 2.0 |

#### 「日別設定」（clinic_date_overrides）
| 列 | 内容 | 例 |
|---|---|---|
| A: clinic_id | 外勤先ID | 1 |
| B: date | 日付（ISO形式） | 2026-03-07 |
| C: required_doctors | 必要人数（0=休診/1=通常/2=2人体制） | 2 |

#### 「設定」
| 列 | 内容 | 例 |
|---|---|---|
| A: key | 設定キー | admin_password |
| B: value | 値 | (SHA-256ハッシュ) |

#### 「希望_YYYY-MM」（月ごとに自動作成）
| 列 | 内容 | 例 |
|---|---|---|
| A: doctor_id | 医員ID | 1 |
| B: doctor_name | 医員名 | 田中太郎 |
| C: ng_dates | ×日（カンマ区切り） | 2026-03-07,2026-03-21 |
| D: avoid_dates | △日（カンマ区切り） | 2026-03-14 |
| E: preferred_clinics | 希望外勤先ID（カンマ区切り） | 1,4 |
| F: updated_at | 更新日時 | 2026-02-16 10:30:00 |

#### 「スケジュール_YYYY-MM」（月ごとに自動作成）
| 列 | 内容 | 例 |
|---|---|---|
| A: id | 連番 | 1 |
| B: plan_name | プラン名 | 案A: 給与均等重視 |
| C: assignments | 割当JSON | [{"date":"...","clinic_id":1,...}] |
| D: total_variance | 報酬分散 | 15000 |
| E: satisfaction_score | 満足度 | 85.5 |
| F: is_confirmed | 確定フラグ | FALSE |
| G: created_at | 作成日時 | 2026-02-16 10:30:00 |

### 実装方針

`database.py` を gspread 版に差し替え。app.py / optimizer.py / pages/ は変更不要。

```python
import gspread
import streamlit as st

@st.cache_resource
def get_spreadsheet():
    credentials = st.secrets["gcp_service_account"]
    gc = gspread.service_account_from_dict(dict(credentials))
    return gc.open("外勤調整データ")
```

### GCP設定手順

1. GCP プロジェクト作成（https://console.cloud.google.com/）
2. Google Sheets API / Google Drive API を有効化
3. サービスアカウント作成 → JSONキー発行
4. スプレッドシート作成 → サービスアカウントを「編集者」として共有
5. Streamlit Cloud の Secrets にJSONキー内容を登録

```toml
[gcp_service_account]
type = "service_account"
project_id = "gaikin-scheduler"
private_key_id = "xxxx"
private_key = "-----BEGIN PRIVATE KEY-----\nxxxx\n-----END PRIVATE KEY-----\n"
client_email = "gaikin-app@gaikin-scheduler.iam.gserviceaccount.com"
client_id = "xxxx"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"

spreadsheet_key = "（スプレッドシートURLの /d/ と /edit の間の文字列）"
```

### セキュリティ

- スプレッドシートの共有は特定のGoogleアカウントのみに限定
- GCPのJSONキーをGitHubにpushしない
- Streamlit CloudのSecrets機能でキー情報を管理
- 扱うデータは医員名・希望・報酬額程度（患者情報は含まない）
