"""
Google スプレッドシートの初期フォーマットを作成するスクリプト

使い方:
  1. GCPのサービスアカウントJSONキーを credentials/credentials.json として配置
  2. python setup_spreadsheet.py <マスタ用スプレッドシートURL>
  3. python setup_spreadsheet.py <マスタ用スプレッドシートURL> --with-samples

オプション:
  --with-samples  サンプルデータ行を各シートに追加（初回セットアップ用）

※ このスクリプトはローカルで1回だけ実行するものです
※ 通常は init_db() が自動でシート・ヘッダーを作成するため不要です
"""
import gspread

CREDENTIALS_FILE = "credentials/credentials.json"

# マスタスプレッドシートのシート定義（database/connection.py の SHEET_HEADERS と同期）
MASTER_SHEETS = {
    "医員マスタ": {
        "headers": [
            "id", "name", "account", "account_name", "email",
            "password_hash", "is_active", "created_at", "max_assignments", "job_rank",
        ],
        "samples": [
            [1, "山田太郎", "2020", "2020", "yamada@example.com", "", 1, "2026-01-01T00:00:00", 2, 3],
            [2, "鈴木花子", "2022", "suzuki", "suzuki@example.com", "", 1, "2026-01-01T00:00:00", 4, 2],
        ],
    },
    "外勤先マスタ": {
        "headers": [
            "id", "name", "fee", "frequency", "preferred_doctors",
            "fixed_doctors", "excluded_doctors", "is_active", "created_at",
            "effort_cost", "work_hours", "time_slot", "location",
        ],
        "samples": [
            [1, "鴨川病院", 75000, "weekly", "", "", "", 1, "2026-01-01T00:00:00", 1, 2.5, "AM", "鴨川市"],
            [2, "沼南", 100000, "biweekly_odd", "", "", "", 1, "2026-01-01T00:00:00", 6, 5.0, "ALL", "柏市"],
        ],
    },
    "優先度マスタ": {
        "headers": ["doctor_id", "clinic_id", "weight"],
        "samples": [
            [1, 1, 3.0],
            [1, 2, 1.0],
            [2, 1, 0.0],
        ],
    },
    "日別設定": {
        "headers": ["clinic_id", "date", "required_doctors"],
        "samples": [],
    },
    "設定": {
        "headers": ["key", "value"],
        "samples": [
            ["open_month", "2026-04"],
            ["input_deadline", "2026-03-25"],
        ],
    },
    "学習テーブル": {
        "headers": [
            "社員ID", "年月", "週日付",
            "採用年度", "役職ランク",
            "過去3ヶ月平均労力コスト", "前週労力コスト",
            "労力コスト最大累計回数", "直近労力コスト最大からの経過週",
            "前週給与", "過去3ヶ月平均給与", "過去3ヶ月累積給与",
            "労力コスト",
        ],
        "samples": [],
    },
    "適合学習テーブル": {
        "headers": [
            "社員ID", "外勤先ID", "年月", "週日付",
            "採用年度", "役職ランク",
            "過去3ヶ月平均労力コスト", "過去3ヶ月平均給与",
            "過去3ヶ月割当回数", "当月累積給与",
            "外勤先_労力コスト", "外勤先_給与", "外勤先_勤務時間", "外勤先_時間帯",
            "労力差", "過去ペア回数", "優先度重み", "給与ランク積",
            "割当結果",
        ],
        "samples": [],
    },
}

# 運用データスプレッドシートの月別シート（アプリが自動作成）
OPERATIONAL_SHEETS_INFO = {
    "希望_YYYY-MM": [
        "doctor_id", "doctor_name", "ng_dates", "avoid_dates",
        "preferred_clinics", "date_clinic_requests", "free_text", "updated_at",
    ],
    "スケジュール_YYYY-MM": [
        "id", "plan_name", "assignments", "total_variance",
        "satisfaction_score", "is_confirmed", "created_at",
    ],
}

# ---- ガイドシートの内容 ----

GUIDE_SHEET_NAME = "ガイド"

GUIDE_ROWS = [
    ["■ マスタスプレッドシート ガイド"],
    ["各シートに直接入力できます。記載例と注意点を参照してください。"],
    ["※ このシートは読み取り専用です。編集しないでください。"],
    [],
    ["════════════════════════════════════════"],
    ["■ 医員マスタ"],
    ["════════════════════════════════════════"],
    [],
    ["列名", "説明", "備考"],
    ["id", "連番（1から）", "重複不可"],
    ["name", "医員名", ""],
    ["account", "医員ID（入局年度）", "管理者が設定。変更不可"],
    ["account_name", "ログイン用アカウント名", "初期値=account。医員が後から変更可能"],
    ["email", "メールアドレス", "通知送信用"],
    ["password_hash", "パスワードハッシュ", "空欄のままでOK。アプリ起動時に初期PW「1111」で自動設定"],
    ["is_active", "有効/無効", "1=有効, 0=無効"],
    ["created_at", "作成日時", "例: 2026-01-01T00:00:00"],
    ["max_assignments", "月あたりの最大外勤回数", "1〜5の整数"],
    ["job_rank", "役職ランク", "0=未設定, 1=レジデント, 2=大学院生, 3=フェロー"],
    [],
    ["記載例:"],
    ["id", "name", "account", "account_name", "email", "password_hash", "is_active", "created_at", "max_assignments", "job_rank"],
    [1, "山田太郎", "2020", "2020", "yamada@example.com", "", 1, "2026-01-01T00:00:00", 2, 3],
    [2, "鈴木花子", "2022", "suzuki", "suzuki@example.com", "", 1, "2026-01-01T00:00:00", 4, 2],
    [],
    ["════════════════════════════════════════"],
    ["■ 外勤先マスタ"],
    ["════════════════════════════════════════"],
    [],
    ["列名", "説明", "備考"],
    ["id", "連番", "重複不可"],
    ["name", "外勤先名", ""],
    ["fee", "日当（円）", ""],
    ["frequency", "頻度", "weekly / biweekly_odd(隔週奇数) / biweekly_even(隔週偶数) / first_only(第1週のみ) / last_only(最終週のみ) / irregular(不定期。日別設定で対象日を指定)"],
    ["preferred_doctors", "（現在未使用）", "空欄で可"],
    ["fixed_doctors", "（現在未使用）", "空欄で可。優先度マスタで管理"],
    ["excluded_doctors", "（現在未使用）", "空欄で可。優先度マスタで管理"],
    ["is_active", "有効/無効", "1=有効, 0=無効"],
    ["created_at", "作成日時", "例: 2026-01-01T00:00:00"],
    ["effort_cost", "労力コスト", "1〜10の整数。ML再調整で使用"],
    ["work_hours", "勤務時間（h）", "小数可（例: 2.5）"],
    ["time_slot", "時間帯", "AM / PM / ALL（空欄可）"],
    ["location", "勤務地", ""],
    [],
    ["記載例:"],
    ["id", "name", "fee", "frequency", "preferred_doctors", "fixed_doctors", "excluded_doctors",
     "is_active", "created_at", "effort_cost", "work_hours", "time_slot", "location"],
    [1, "鴨川病院", 75000, "weekly", "", "", "", 1, "2026-01-01T00:00:00", 1, 2.5, "AM", "鴨川市"],
    [2, "沼南", 100000, "biweekly_odd", "", "", "", 1, "2026-01-01T00:00:00", 6, 5.0, "ALL", "柏市"],
    [],
    ["════════════════════════════════════════"],
    ["■ 優先度マスタ"],
    ["════════════════════════════════════════"],
    [],
    ["医員と外勤先の割り当て優先度を管理します。アプリのマトリクスUIでも編集可能です。"],
    [],
    ["weight の意味:"],
    ["weight", "ラベル", "説明"],
    ["3.0", "固定", "この外勤先は固定メンバーのみ割当。月1回以上必須（ハード制約）"],
    ["2.0", "指名", "できれば来てほしい（ソフト制約）"],
    ["1.0", "任意", "デフォルト。未登録の組み合わせも任意扱い"],
    ["0.0", "除外", "割当不可（ハード制約）"],
    [],
    ["注意: 固定(3.0)を設定した外勤先は、固定メンバーのみに割り当てられます（ホワイトリスト）"],
    [],
    ["記載例:"],
    ["doctor_id", "clinic_id", "weight"],
    [1, 1, 3.0],
    [1, 2, 1.0],
    [2, 1, 0.0],
    [],
    ["════════════════════════════════════════"],
    ["■ 日別設定"],
    ["════════════════════════════════════════"],
    [],
    ["特定の日に2人体制や休診を設定します。"],
    [],
    ["列名", "説明", "備考"],
    ["clinic_id", "外勤先ID", "外勤先マスタのidを参照"],
    ["date", "日付", "YYYY-MM-DD形式"],
    ["required_doctors", "必要人数", "1=通常, 2=2人体制, 0=休診"],
    [],
    ["注意: 未登録の日はfrequencyに従い通常(1人)として扱われます"],
    [],
    ["記載例:"],
    ["clinic_id", "date", "required_doctors"],
    [1, "2026-04-05", 2],
    [2, "2026-04-12", 0],
    [],
    ["════════════════════════════════════════"],
    ["■ 設定"],
    ["════════════════════════════════════════"],
    [],
    ["列名", "説明", "備考"],
    ["admin_password", "管理者パスワード", "アプリで設定。直接入力しないでください（SHA-256ハッシュ値）"],
    ["admin_emails", "管理者メール", "カンマ区切りで複数指定可。通知送信先"],
    ["open_month", "希望入力の対象月", "YYYY-MM形式"],
    ["input_deadline", "入力期限", "YYYY-MM-DD形式"],
    [],
    ["記載例:"],
    ["key", "value"],
    ["admin_emails", "admin1@example.com,admin2@example.com"],
    ["open_month", "2026-04"],
    ["input_deadline", "2026-03-25"],
    [],
    ["════════════════════════════════════════"],
    ["■ 学習テーブル / 適合学習テーブル"],
    ["════════════════════════════════════════"],
    [],
    ["ML再調整用の学習データです。アプリが自動生成するため直接入力は不要です。"],
    ["ヘッダーのみ設定してあります。"],
    [],
    ["════════════════════════════════════════"],
    ["■ 運用データスプレッドシート（別スプレッドシート）"],
    ["════════════════════════════════════════"],
    [],
    ["運用データは別のスプレッドシートで管理されます。"],
    ["月別シート（希望_YYYY-MM, スケジュール_YYYY-MM）はアプリが自動作成します。"],
    ["直接入力は不要です。"],
]


def _setup_guide_sheet(sh):
    """ガイドシートを作成（先頭に配置）"""
    existing = [ws.title for ws in sh.worksheets()]
    if GUIDE_SHEET_NAME in existing:
        ws = sh.worksheet(GUIDE_SHEET_NAME)
        ws.clear()
        print(f"  [既存] {GUIDE_SHEET_NAME}（内容をリセット）")
    else:
        # 十分な行数・列数で作成
        max_cols = max(len(row) for row in GUIDE_ROWS if row)
        ws = sh.add_worksheet(title=GUIDE_SHEET_NAME, rows=len(GUIDE_ROWS) + 10, cols=max(max_cols, 15))
        print(f"  [作成] {GUIDE_SHEET_NAME}")

    # 内容を書き込み
    ws.update(GUIDE_ROWS, "A1")

    # 先頭に移動
    sh.reorder_worksheets([ws] + [w for w in sh.worksheets() if w.title != GUIDE_SHEET_NAME])
    print(f"  [配置] {GUIDE_SHEET_NAME} を先頭に移動")


def _setup_sheets(sh, sheets_config, with_samples=False):
    """スプレッドシートにシートを作成/ヘッダーを設定"""
    existing = [ws.title for ws in sh.worksheets()]
    for sheet_name, config in sheets_config.items():
        headers = config["headers"]
        samples = config.get("samples", [])
        if sheet_name in existing:
            ws = sh.worksheet(sheet_name)
            print(f"  [既存] {sheet_name}")
        else:
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=len(headers))
            print(f"  [作成] {sheet_name}")
        current_header = ws.row_values(1)
        if current_header != headers:
            ws.update([headers], "A1")
            print(f"    ヘッダー設定: {len(headers)}列")

        if with_samples and samples:
            # 既存データがなければサンプルを追加
            all_values = ws.get_all_values()
            if len(all_values) <= 1:  # ヘッダーのみ
                ws.update(samples, "A2")
                print(f"    サンプルデータ: {len(samples)}行")
            else:
                print(f"    サンプルスキップ: 既存データ{len(all_values) - 1}行あり")

    # デフォルトの「Sheet1」があれば削除
    for ws in sh.worksheets():
        if ws.title in ("Sheet1", "シート1") and len(sh.worksheets()) > 1:
            sh.del_worksheet(ws)
            print(f"  [削除] {ws.title}")


def main():
    import sys

    gc = gspread.service_account(filename=CREDENTIALS_FILE)

    with_samples = "--with-samples" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("マスタスプレッドシートの初期セットアップスクリプト")
        print()
        print("使い方:")
        print("  python setup_spreadsheet.py <スプレッドシートURL or キー>")
        print("  python setup_spreadsheet.py <スプレッドシートURL or キー> --with-samples")
        print()
        print("オプション:")
        print("  --with-samples  サンプルデータ行を各シートに追加")
        print()
        print("シート構成:")
        print("  ガイド          : 各シートの説明・記載例・注意点")
        for name in MASTER_SHEETS:
            n_samples = len(MASTER_SHEETS[name].get("samples", []))
            sample_note = f"（サンプル: {n_samples}行）" if n_samples else ""
            print(f"  {name:14s}: {len(MASTER_SHEETS[name]['headers'])}列 {sample_note}")
        return

    arg = args[0]
    if "docs.google.com" in arg:
        sh = gc.open_by_url(arg)
    else:
        sh = gc.open_by_key(arg)
    print(f"スプレッドシート「{sh.title}」に接続しました")

    existing = [ws.title for ws in sh.worksheets()]
    print(f"既存シート: {existing}")

    # マスタ系シートがあれば or 空なら → マスタとして初期化
    is_master = any(name in existing for name in MASTER_SHEETS) or len(existing) <= 1
    if is_master:
        print("\n--- ガイドシートを作成 ---")
        _setup_guide_sheet(sh)

        print("\n--- マスタシートを初期化 ---")
        _setup_sheets(sh, MASTER_SHEETS, with_samples=with_samples)
    else:
        print("\n--- 運用データ用スプレッドシートです ---")
        print("月別シート（希望_YYYY-MM, スケジュール_YYYY-MM）はアプリが自動作成します。")

    print("\n完了。シート一覧:")
    for ws in sh.worksheets():
        print(f"  - {ws.title}")


if __name__ == "__main__":
    main()
