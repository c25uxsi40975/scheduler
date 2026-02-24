# 外勤調整システム - 設計書

## システム概要

病院の医員（医師）を土曜日の外勤先に割り当てるスケジューリングシステム。
管理者が外勤先・医員を管理し、医員が希望を入力、PuLP（線形計画法）で最適な割り当てを自動生成する。
さらに RandomForest モデルによる ML 再調整で、労力コストを考慮した最適化が可能。

- **フレームワーク:** Streamlit
- **ホスティング:** Streamlit Cloud
- **DB:** Google Sheets（gspread 経由、マスタ用 + 運用データ用の2ファイル構成）
- **最適化:** PuLP（CBC Solver、タイムリミット30秒）
- **ML再調整:** scikit-learn RandomForest + scipy linear_sum_assignment
- **祝日判定:** jpholiday

---

## ファイル構成

```
scheduler/
├── app.py                          メインエントリポイント（ログイン・ルーティング）
├── optimizer.py                    スケジュール最適化エンジン（PuLP）
├── ml_adjuster.py                  ML再調整エンジン（RandomForest + 線形割当）
├── model.pkl                       学習済みモデル（gitignore対象）
├── database/
│   ├── __init__.py                 再エクスポート（既存importの互換維持）
│   ├── connection.py               接続・キャッシュ・リトライ・共有ユーティリティ
│   ├── master.py                   マスタCRUD（医員・外勤先・優先度・日別設定）
│   ├── operational.py              運用データ（希望・スケジュール・古データ削除）
│   └── auth.py                     認証・設定・パスワード・メール・対象月制御
├── components/
│   ├── __init__.py
│   └── schedule_table.py           共通テーブル表示
├── pages/
│   ├── __init__.py
│   ├── admin_master.py             マスタ管理
│   ├── admin_preferences.py        希望状況一覧
│   ├── admin_generate.py           スケジュール生成
│   ├── admin_schedule.py           確定スケジュール確認
│   ├── admin_ml_adjust.py          ML再調整
│   ├── doctor_input.py             医員希望入力
│   └── doctor_schedule.py          医員スケジュール確認
├── gas/
│   ├── SETUP.md                    GAS設定手順
│   └── reminder.gs                 メールリマインダー・確定通知
├── scripts/
│   ├── seed_data.py                テスト用データ投入
│   └── setup_spreadsheet.py        スプレッドシート初期化（通常は不要）
├── docs/
│   ├── DESIGN.md                   本ファイル
│   └── SETUP_GUIDE.md              デプロイ手順（管理者向け）
├── requirements.txt
└── README.md
```

---

## ファイル間の依存関係

```
app.py
├── database/          (init_db, get_doctors, auth関数)
├── optimizer.py       (get_target_saturdays)
└── pages/
    ├── admin_master.py       → database/, optimizer.py
    ├── admin_preferences.py  → database/, optimizer.py
    ├── admin_generate.py     → database/, optimizer.py, components/
    ├── admin_schedule.py     → database/, components/
    ├── admin_ml_adjust.py    → database/, ml_adjuster.py, components/
    ├── doctor_input.py       → database/, optimizer.py
    └── doctor_schedule.py    → database/, components/

ml_adjuster.py → optimizer.py (get_target_saturdays, get_clinic_dates)
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
    └── アカウント名 + パスワード入力画面
        └── 認証成功 → 医員タブ画面
```

### 認証方式

- **管理者**: 共通パスワード1つ（SHA-256ハッシュ化して「設定」シートに保存）
- **医員**: 個別アカウント（アカウント名 + 個別パスワード）
  - `account`: 医員ID（入局年度）。管理者が設定、変更不可
  - `account_name`: ログイン用アカウント名。初期値 = account、医員が変更可能
  - `password_hash`: SHA-256ハッシュ。管理者が初期パスワードを設定
- セッション管理: `st.session_state` で `role`, `admin_authenticated`, `doctor_authenticated`, `doctor_id` を保持
- ログアウト: ヘッダーのボタンでロール選択画面に戻る

---

## ユーザー種別とタブ構成

| ユーザー | タブ |
|---|---|
| 管理者 | マスタ管理 / 希望状況一覧 / スケジュール生成 / スケジュール確認 / ML再調整 |
| 医員 | 希望入力 / スケジュール確認 |

---

## データベース設計（Google Sheets）

### スプレッドシート構成

| スプレッドシート | 用途 | Secretsキー |
|---|---|---|
| マスタ | 医員・外勤先・優先度・日別設定・設定 | `spreadsheet_key` |
| 運用データ | 希望_YYYY-MM / スケジュール_YYYY-MM | `spreadsheet_key_operational` |

### シート一覧

| シート | 用途 | 主なカラム |
|---|---|---|
| 医員マスタ | 医員の一覧 | id, name, account, account_name, email, password_hash, is_active, created_at, max_assignments, job_rank |
| 外勤先マスタ | 外勤先の一覧 | id, name, fee, frequency, preferred_doctors, fixed_doctors, is_active, created_at, effort_cost, work_hours, time_slot, location |
| 優先度マスタ | 医員-外勤先の優先度 | doctor_id, clinic_id, weight(◎=2.0/○=1.0/×=0.0) |
| 日別設定 | 外勤先の日別オーバーライド | clinic_id, date, required_doctors(0=休診/1=通常/2=2人体制) |
| 設定 | アプリ設定 | key, value（admin_password, open_month, input_deadline 等） |
| 希望_YYYY-MM | 医員の月次希望（月ごと自動作成） | doctor_id, doctor_name, ng_dates, avoid_dates, preferred_clinics, date_clinic_requests, free_text, updated_at |
| スケジュール_YYYY-MM | 生成スケジュール（月ごと自動作成） | id, plan_name, assignments(JSON), total_variance, satisfaction_score, is_confirmed, created_at |

### 重要: カラム順序の安全性

- `init_db()` は不足カラムを既存ヘッダーの**末尾**に追加する
- そのため実際のカラム順序は `SHEET_HEADERS` と異なる場合がある
- 書き込み時は必ず `ws.row_values(1)` で実際のヘッダーを取得して使用する
- `SHEET_HEADERS` の固定インデックスでの書き込みは禁止

### 外勤先の頻度区分

| frequency値 | 意味 |
|---|---|
| `weekly` | 毎週 |
| `biweekly_odd` | 隔週（奇数週） |
| `biweekly_even` | 隔週（偶数週） |
| `first_only` | 第1週のみ |
| `last_only` | 最終週のみ |

### 医員-外勤先 優先度（◎○×方式）

| 記号 | weight値 | 意味 | 最適化への反映 |
|---|---|---|---|
| ◎ | 2.0 | 月1回以上必ず行く | ハード制約（必ず1回以上割り当て） |
| ○ | 1.0 | 行くときもある | ソフト制約（割り当て候補、デフォルト） |
| × | 0.0 | まったく行かない | ハード制約（割り当て禁止） |

### 外勤先の日別設定

| required_doctors | 意味 |
|---|---|
| 0 | 休診（スロットから除外） |
| 1 | 通常（デフォルト、保存せず） |
| 2 | 2人体制 |

### 医員の日程希望（○△×方式）

| 記号 | 保存先 | 意味 | 最適化への反映 |
|---|---|---|---|
| ○ | (該当なし) | 出勤可能 | 制約なし |
| △ | avoid_dates | できれば避けたい | ソフトペナルティ |
| × | ng_dates | NG（出勤不可） | ハード制約（割り当て禁止） |

---

## 最適化エンジン設計（optimizer.py）

### 制約条件（ハード制約）

| # | 制約 | 内容 |
|---|---|---|
| 1 | スロット人数 | 各外勤先・各日に必要人数を割り当て |
| 2 | 1日1外勤 | 各医員は同一日に最大1ヶ所 |
| 3 | ×日除外 | NG指定した日には割り当てない |
| 4 | ×外勤先除外 | 優先度×の外勤先には割り当てない |
| 5 | ◎外勤先必須 | 優先度◎の外勤先には月1回以上割り当て |
| 6 | 固定メンバー | 固定メンバーはNG日を除き必ず割り当て |
| 7 | 月回数上限 | max_assignments > 0 の場合、月回数を制限 |

### 目的関数の構成要素（ソフト制約）

| 要素 | 内容 |
|---|---|
| `variance_term` | 報酬ばらつき最小化（累計報酬含む） |
| `preference_term` | 医員の希望外勤先マッチ |
| `nomination_term` | 外勤先の指名医員マッチ |
| `priority_term` | 優先度スコア加算（◎=2, ○=1） |
| `avoid_penalty` | △日ペナルティ |
| `count_variance` | 外勤回数のばらつき最小化 |
| `date_clinic_bonus` | 日別外勤先希望マッチ |

### 3つの生成モード

| モード | 重視ポイント |
|---|---|
| `balanced` | 給与均等 |
| `preference` | 医員希望 |
| `affinity` | 優先度 |

---

## ML再調整エンジン設計（ml_adjuster.py）

### 概要

PuLP生成後の最終調整として、蓄積データから学習した RandomForest モデルで
医員ごとの「妥当な労力コスト」を予測し、Hungarian Algorithm で最適マッチングを行う。

### 9特徴量

| # | 特徴量 | 計算方法 |
|---|--------|----------|
| 1 | 採用年度 | `int(doctor["account"])` |
| 2 | 役職ランク | `doctor["job_rank"]` (0→NaN、Imputerが補完) |
| 3 | 過去3ヶ月平均労力コスト | 3ヶ月窓内の外勤先effort_costの平均 |
| 4 | 前週労力コスト | 直近の外勤先effort_cost |
| 5 | 労力コスト最大累計回数 | effort_cost>=10の全回数 |
| 6 | 直近労力コスト最大からの経過週 | 最後のeffort_cost>=10からの週数 |
| 7 | 前週給与 | 直近の外勤先fee |
| 8 | 過去3ヶ月平均給与 | 3ヶ月窓内の外勤先feeの平均 |
| 9 | 過去3ヶ月累積給与 | 3ヶ月窓内の外勤先feeの合計 |

### 日次割当アルゴリズム

各土曜日について順次処理:

1. 固定メンバーを事前割当（NG日でない限り）
2. 残りスロットに対してコスト行列を構築:
   - `cost[i][j] = |prediction[doctor_i] - clinic_j.effort_cost|`
   - NG日・×外勤先 → cost = INF (1e9)
3. `linear_sum_assignment(cost_matrix)` で最適マッチング
4. `max_assignments` 超過チェック（日をまたいで累積）

事後チェック: ◎制約（must clinics）の充足確認 → 未充足は警告表示

---

## 各ファイルの機能詳細

### app.py - メインエントリポイント

- ページ設定（タイトル、レイアウト）
- 2スプレッドシート構成の必須チェック
- DB初期化（`init_db()` でシート・ヘッダー・初期パスワードを自動設定）
- ロール選択画面（管理者 / 医員）
- 管理者パスワード認証（初回は設定画面、以降はログイン画面）
- 医員個別認証（アカウント名 + パスワード）
- 認証後：ヘッダーに対象月選択・ログアウトボタン、メインにタブ表示
- 医員設定画面（アカウント名変更、パスワード変更、メールアドレス設定）

### database/ - データベース層

- **connection.py**: gspread接続管理（マスタ/運用の2系統）、リトライ付きAPI呼び出し、データキャッシュ、init_db()
- **master.py**: 医員・外勤先のCRUD、優先度設定、日別オーバーライド一括保存
- **operational.py**: 月別希望シートの管理、スケジュールの保存/確定/削除、確定スケジュール一括取得、古データ自動クリーンアップ
- **auth.py**: 管理者パスワード、医員個別パスワード、アカウント名認証、メール設定、対象月・入力期限制御

### optimizer.py - PuLP最適化エンジン

- 対象土曜日の算出（祝日除外）
- 外勤先頻度に応じた対象日フィルタリング
- PuLPによる0-1整数計画問題の定式化・求解
- 固定メンバー・◎必須・×禁止・△ペナルティ・月回数上限
- 日別外勤先希望ボーナス
- 3モード×1回の一括プラン生成

### ml_adjuster.py - ML再調整エンジン

- model.pkl の遅延ロード
- 医員ごとの9特徴量計算（過去の確定スケジュールから履歴を抽出）
- RandomForest による労力コスト予測
- 日次 linear_sum_assignment で最適割当
- 制約の尊重（NG日、×外勤先、固定メンバー、月回数上限）

### components/schedule_table.py - 共通コンポーネント

- スケジュールデータをカレンダー形式DataFrameに変換
- 行=外勤先、列=日付（MM/DD(曜日)）、セル=医員名

### pages/ - 画面モジュール

- **admin_master.py**: 希望入力対象月設定、医員の追加/編集/削除（ID・アカウント・パスワード・メール・回数上限・役職）、外勤先の追加/編集（テンプレート選択対応・日当・頻度・労力コスト・勤務時間・時間帯・勤務地）、指名・固定メンバー・優先度設定、日別外勤先希望、日別設定
- **admin_preferences.py**: 全医員の希望入力状況一覧
- **admin_generate.py**: 3案一括生成、手動調整、確定
- **admin_schedule.py**: 確定スケジュール表示、CSV出力
- **admin_ml_adjust.py**: ML再調整の実行・結果表示・確定スケジュールとの比較・保存
- **doctor_input.py**: 医員希望入力（日程○△×、希望外勤先、日別外勤先希望、自由記述）
- **doctor_schedule.py**: 医員スケジュール確認
