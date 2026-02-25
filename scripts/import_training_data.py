"""training_table.csv をGoogle Sheetsの学習テーブルにインポート

使い方:
  python import_training_data.py <マスタスプレッドシートURL or キー>

※ このスクリプトはローカルで1回だけ実行するものです
"""
import csv
import gspread

CREDENTIALS_FILE = "credentials/credentials.json"


def main():
    import sys
    if len(sys.argv) < 2:
        print("使い方: python import_training_data.py <マスタスプレッドシートURL or キー>")
        return

    gc = gspread.service_account(filename=CREDENTIALS_FILE)
    arg = sys.argv[1]
    sh = gc.open_by_url(arg) if "docs.google.com" in arg else gc.open_by_key(arg)
    print(f"スプレッドシート「{sh.title}」に接続しました")

    try:
        ws = sh.worksheet("学習テーブル")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="学習テーブル", rows=1500, cols=13)

    with open("training_table.csv", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows = list(reader)

    ws.update([headers] + rows, "A1")
    print(f"{len(rows)}行をインポートしました")


if __name__ == "__main__":
    main()
