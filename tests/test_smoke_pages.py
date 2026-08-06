"""管理者ページのヘッドレス・スモークテスト（読み取り専用・実データ）。

Streamlit の AppTest で各ページを実際に実行し、例外なく描画されるかを確認する。
書き込みは読取専用モードで全ブロックされるため、本番データは一切変更されない。

前提: .streamlit/secrets.toml が配置済みであること（実データ読み取りに必要）。
      未配置なら自動でスキップする。

実行:
    .venv/bin/python tests/test_smoke_pages.py
"""
import os
import sys
from datetime import date

# 実データ読取・書込ブロック
os.environ["SCHEDULER_READONLY"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SECRETS = os.path.join(ROOT, ".streamlit", "secrets.toml")

# 中身は読まない。存在チェックのみ。
if not os.path.exists(SECRETS):
    print("⏭  スキップ: .streamlit/secrets.toml が未配置です（実データに接続できません）")
    print("   Streamlit Cloud の Secrets をコピーして配置すると、このスモークテストが実データで走ります。")
    sys.exit(0)

from streamlit.testing.v1 import AppTest

_today = date.today()
YEAR, MONTH = _today.year, _today.month
TARGET_MONTH = f"{YEAR}-{MONTH:02d}"


def _run_render(module_name):
    """指定ページの render() を admin 認証済みで実行し AppTest を返す。"""
    def _script():
        import streamlit as st
        st.session_state["admin_authenticated"] = True
        import importlib
        from database import init_db
        init_db()  # 読取専用なら書き込みなし
        mod = importlib.import_module(module_name)
        mod.render(TARGET_MONTH, YEAR, MONTH)

    at = AppTest.from_function(_script)
    at.run(timeout=90)
    return at


def _run_app():
    """app.py 全体（ログイン画面）を実行し、import・トップレベル描画を検証。"""
    at = AppTest.from_file(os.path.join(ROOT, "app.py"))
    at.run(timeout=90)
    return at


def _assert_ok(at, label):
    if at.exception:
        print(f"❌ {label}: 例外発生")
        for ex in at.exception:
            print(f"   {ex.type}: {ex.value}")
        return False
    print(f"✅ {label}: 例外なし描画OK")
    return True


if __name__ == "__main__":
    results = []
    results.append(_assert_ok(_run_app(), "app.py（ログイン画面）"))
    results.append(_assert_ok(_run_render("pages.admin_clinic_master"), "外勤先マスタ"))
    results.append(_assert_ok(_run_render("pages.admin_doctor_master"), "医員マスタ"))

    from database import readonly_blocked_writes
    print(f"\n読取専用でブロックした書き込み: {readonly_blocked_writes()} 件")

    if all(results):
        print(f"\n✅ 全 {len(results)} ページ スモーク成功")
        sys.exit(0)
    print(f"\n❌ {results.count(False)} 件失敗")
    sys.exit(1)
