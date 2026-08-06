"""読み取り専用モード（_ReadOnlyWorksheet プロキシ）の単体テスト。

secrets もネットワークも不要でオフライン実行できる。
    .venv/bin/python tests/test_readonly_guard.py
"""
import os
import sys

# プロジェクトルートを import パスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import connection as conn


class FakeWorksheet:
    """gspread Worksheet の最小モック。書き込みが呼ばれたら記録する。"""

    def __init__(self):
        self.title = "医員マスタ"
        self.writes = []

    # --- 読み取り ---
    def get_all_records(self):
        return [{"id": 1, "name": "田中太郎"}]

    def row_values(self, n):
        return ["id", "name"]

    def col_values(self, n):
        return ["id", "1"]

    def get_all_values(self):
        return [["id", "name"], ["1", "田中太郎"]]

    # --- 書き込み（呼ばれてはいけない） ---
    def update(self, *a, **k):
        self.writes.append(("update", a))

    def append_row(self, *a, **k):
        self.writes.append(("append_row", a))

    def append_rows(self, *a, **k):
        self.writes.append(("append_rows", a))

    def update_cell(self, *a, **k):
        self.writes.append(("update_cell", a))

    def batch_update(self, *a, **k):
        self.writes.append(("batch_update", a))

    def delete_rows(self, *a, **k):
        self.writes.append(("delete_rows", a))


def test_reads_pass_through():
    fake = FakeWorksheet()
    proxy = conn._ReadOnlyWorksheet(fake, name="医員マスタ")
    assert proxy.get_all_records() == [{"id": 1, "name": "田中太郎"}]
    assert proxy.row_values(1) == ["id", "name"]
    assert proxy.col_values(1) == ["id", "1"]
    assert proxy.get_all_values() == [["id", "name"], ["1", "田中太郎"]]
    # プロパティは素通し
    assert proxy.title == "医員マスタ"
    print("OK: 読み取り・プロパティは素通し")


def test_writes_are_blocked():
    fake = FakeWorksheet()
    proxy = conn._ReadOnlyWorksheet(fake, name="医員マスタ")
    before = conn.readonly_blocked_writes()

    # すべての書き込みメソッドを呼ぶ（例外なく no-op のはず）
    proxy.update([["a"]], "A1")
    proxy.append_row(["x"])
    proxy.append_rows([["x"]])
    proxy.update_cell(1, 1, "x")
    proxy.batch_update([{"range": "A1", "values": [["x"]]}])
    proxy.delete_rows(2)

    # 実 worksheet には一切書き込まれていない
    assert fake.writes == [], f"書き込みが漏れた: {fake.writes}"
    # ブロック回数が 6 増えている
    delta = conn.readonly_blocked_writes() - before
    assert delta == 6, f"ブロック数が想定外: {delta}"
    print(f"OK: 書き込み6件をすべてブロック（実シート非書込み）")


def test_empty_stub_for_missing_sheet():
    proxy = conn._ReadOnlyWorksheet(None, name="希望_2099-01")
    # 存在しないシートは空を返す（クラッシュしない）
    assert proxy.get_all_records() == []
    assert proxy.row_values(1) == []
    assert proxy.col_values(1) == []
    assert proxy.get_all_values() == []
    # 書き込みも no-op
    proxy.append_row(["x"])
    print("OK: 欠損シートは空スタブとして安全に動作")


def test_is_readonly_env_parsing():
    for val, expected in [("1", True), ("true", True), ("YES", True),
                          ("on", True), ("0", False), ("", False), ("no", False)]:
        os.environ["SCHEDULER_READONLY"] = val
        assert conn.is_readonly() is expected, f"{val!r} -> {conn.is_readonly()}"
    os.environ.pop("SCHEDULER_READONLY", None)
    assert conn.is_readonly() is False
    print("OK: 環境変数のON/OFF判定")


def test_wrap_readonly_toggles_with_env():
    fake = FakeWorksheet()
    os.environ.pop("SCHEDULER_READONLY", None)
    assert conn._wrap_readonly(fake) is fake  # OFF: 素通し
    os.environ["SCHEDULER_READONLY"] = "1"
    wrapped = conn._wrap_readonly(fake, name="医員マスタ")
    assert isinstance(wrapped, conn._ReadOnlyWorksheet)  # ON: ラップ
    assert conn._wrap_readonly(wrapped) is wrapped       # 二重ラップしない
    os.environ.pop("SCHEDULER_READONLY", None)
    print("OK: env で _wrap_readonly が切り替わる（二重ラップ防止）")


def test_resync_skips_in_readonly():
    """読取専用モードでは平日カレンダー再同期が GAS へ POST しない。"""
    from database import weekday
    os.environ["SCHEDULER_READONLY"] = "1"
    orig_post = weekday.requests.post

    def _boom(*a, **k):
        raise AssertionError("readonly なのに POST された")

    weekday.requests.post = _boom
    try:
        assert weekday.resync_weekday_calendar(
            "weekday_1", "テスト", year_months=["2026-08"]) is None
    finally:
        weekday.requests.post = orig_post
        os.environ.pop("SCHEDULER_READONLY", None)
    print("OK: 読取専用では resync が POST しない")


def test_resync_no_post_without_confirmed_months():
    """確定月が無ければ resync は POST しない（カレンダーイベントが存在しないため）。"""
    from database import weekday
    from database import auth
    os.environ.pop("SCHEDULER_READONLY", None)
    orig_conf = auth.get_weekday_confirmed_months
    orig_post = weekday.requests.post
    auth.get_weekday_confirmed_months = lambda section: []

    def _boom(*a, **k):
        raise AssertionError("確定月ゼロなのに POST された")

    weekday.requests.post = _boom
    try:
        assert weekday.resync_weekday_calendar("weekday_1", "テスト") is None
    finally:
        auth.get_weekday_confirmed_months = orig_conf
        weekday.requests.post = orig_post
    print("OK: 確定月が無ければ resync は POST しない")


if __name__ == "__main__":
    tests = [
        test_reads_pass_through,
        test_writes_are_blocked,
        test_empty_stub_for_missing_sheet,
        test_is_readonly_env_parsing,
        test_wrap_readonly_toggles_with_env,
        test_resync_skips_in_readonly,
        test_resync_no_post_without_confirmed_months,
    ]
    for t in tests:
        t()
    print(f"\n✅ 全 {len(tests)} テスト成功")
