"""
Google スプレッドシートの初期フォーマットを作成するスクリプト

使い方:
  1. GCPのサービスアカウントJSONキーを credentials/credentials.json として配置
  2. python setup_spreadsheet.py <マスタ用スプレッドシートURL>
  3. python setup_spreadsheet.py <運用データ用スプレッドシートURL>

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
    },
    "外勤先マスタ": {
        "headers": [
            "id", "name", "fee", "frequency", "preferred_doctors",
            "fixed_doctors", "is_active", "created_at",
            "effort_cost", "work_hours", "time_slot", "location",
        ],
    },
    "優先度マスタ": {
        "headers": ["doctor_id", "clinic_id", "weight"],
    },
    "日別設定": {
        "headers": ["clinic_id", "date", "required_doctors"],
    },
    "設定": {
        "headers": ["key", "value"],
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


def _setup_sheets(sh, sheets_config):
    """スプレッドシートにシートを作成/ヘッダーを設定"""
    existing = [ws.title for ws in sh.worksheets()]
    for sheet_name, config in sheets_config.items():
        headers = config["headers"]
        if sheet_name in existing:
            ws = sh.worksheet(sheet_name)
            print(f"  [既存] {sheet_name}")
        else:
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=len(headers))
            print(f"  [作成] {sheet_name}")
        current_header = ws.row_values(1)
        if current_header != headers:
            ws.update([headers], "A1")
            print(f"    ヘッダー設定: {headers}")

    # デフォルトの「Sheet1」があれば削除
    for ws in sh.worksheets():
        if ws.title in ("Sheet1", "シート1") and len(sh.worksheets()) > 1:
            sh.del_worksheet(ws)
            print(f"  [削除] {ws.title}")


def main():
    import sys

    gc = gspread.service_account(filename=CREDENTIALS_FILE)

    if len(sys.argv) < 2:
        print("使い方:")
        print("  python setup_spreadsheet.py <スプレッドシートURL or キー>")
        print()
        print("マスタ用・運用データ用それぞれ1回ずつ実行してください。")
        print("（通常は init_db() が自動作成するため不要です）")
        return

    arg = sys.argv[1]
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
        print("\n--- マスタシートを初期化 ---")
        _setup_sheets(sh, MASTER_SHEETS)
    else:
        print("\n--- 運用データ用スプレッドシートです ---")
        print("月別シート（希望_YYYY-MM, スケジュール_YYYY-MM）はアプリが自動作成します。")

    print("\n完了。シート一覧:")
    for ws in sh.worksheets():
        print(f"  - {ws.title}")


if __name__ == "__main__":
    main()
