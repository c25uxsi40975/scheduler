"""
Google スプレッドシートの初期フォーマットを作成するスクリプト

使い方:
  1. GCPのサービスアカウントJSONキーを credentials.json として配置
  2. スプレッドシート「外勤調整データ」を作成し、サービスアカウントに共有済み
  3. python setup_spreadsheet.py を実行

※ このスクリプトはローカルで1回だけ実行するものです
"""
import gspread

# ---- 設定 ----
CREDENTIALS_FILE = "credentials/credentials.json"
SPREADSHEET_NAME = "外勤調整データ"

# ---- シート定義 ----
SHEETS = {
    "医員マスタ": {
        "headers": ["id", "name", "is_active", "created_at"],
        "widths": [50, 150, 80, 180],
        "description": "医員の一覧。is_active: 1=有効, 0=無効",
    },
    "外勤先マスタ": {
        "headers": ["id", "name", "fee", "frequency", "preferred_doctors", "is_active", "created_at"],
        "widths": [50, 150, 80, 120, 200, 80, 180],
        "description": "外勤先の一覧。frequency: weekly/biweekly_odd/biweekly_even/first_only/last_only",
    },
    "優先度マスタ": {
        "headers": ["doctor_id", "clinic_id", "weight"],
        "widths": [80, 80, 80],
        "description": "医員-外勤先の優先度。weight: 2.0=◎, 1.0=○, 0.0=×",
    },
    "日別設定": {
        "headers": ["clinic_id", "date", "required_doctors"],
        "widths": [80, 120, 120],
        "description": "外勤先の日別オーバーライド。required_doctors: 0=休診, 1=通常, 2=2人体制",
    },
    "設定": {
        "headers": ["key", "value"],
        "widths": [200, 400],
        "description": "アプリ設定。admin_password, doctor_password (SHA-256ハッシュ)",
    },
}

# 月別シート（必要時にアプリが自動作成するため、ここではサンプルのみ表示）
MONTHLY_SHEETS = {
    "希望_YYYY-MM": {
        "headers": ["doctor_id", "doctor_name", "ng_dates", "avoid_dates", "preferred_clinics", "updated_at"],
        "description": "医員の月次希望（×日、△日、希望外勤先）。アプリが月ごとに自動作成。",
    },
    "スケジュール_YYYY-MM": {
        "headers": ["id", "plan_name", "assignments", "total_variance", "satisfaction_score", "is_confirmed", "created_at"],
        "description": "生成スケジュール案。アプリが月ごとに自動作成。",
    },
}


def main():
    import sys

    gc = gspread.service_account(filename=CREDENTIALS_FILE)

    # コマンドライン引数でスプレッドシートURLまたはキーを指定可能
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if "docs.google.com" in arg:
            sh = gc.open_by_url(arg)
        else:
            sh = gc.open_by_key(arg)
        print(f"スプレッドシート「{sh.title}」に接続しました（URL/キー指定）")
    else:
        try:
            sh = gc.open(SPREADSHEET_NAME)
            print(f"スプレッドシート「{SPREADSHEET_NAME}」に接続しました")
        except gspread.SpreadsheetNotFound:
            print(f"エラー: スプレッドシート「{SPREADSHEET_NAME}」が見つかりません")
            print()
            print("URLを指定して実行してください:")
            print("  python setup_spreadsheet.py https://docs.google.com/spreadsheets/d/xxxxx/edit")
            return

    # 既存のシート名一覧
    existing = [ws.title for ws in sh.worksheets()]
    print(f"既存シート: {existing}")

    # 固定シートの作成
    for sheet_name, config in SHEETS.items():
        if sheet_name in existing:
            ws = sh.worksheet(sheet_name)
            print(f"  [既存] {sheet_name}")
        else:
            ws = sh.add_worksheet(
                title=sheet_name,
                rows=100,
                cols=len(config["headers"])
            )
            print(f"  [作成] {sheet_name}")

        # ヘッダー設定
        current_header = ws.row_values(1)
        if current_header != config["headers"]:
            ws.update([config["headers"]], "A1")
            print(f"    ヘッダー設定: {config['headers']}")

    # デフォルトの「Sheet1」があれば削除
    for ws in sh.worksheets():
        if ws.title in ("Sheet1", "シート1") and len(sh.worksheets()) > 1:
            sh.del_worksheet(ws)
            print(f"  [削除] {ws.title}")

    print()
    print("初期化完了。以下のシートが作成されました:")
    for ws in sh.worksheets():
        print(f"  - {ws.title}")

    print()
    print("月別シート（希望_YYYY-MM, スケジュール_YYYY-MM）はアプリ使用時に自動作成されます。")


if __name__ == "__main__":
    main()
