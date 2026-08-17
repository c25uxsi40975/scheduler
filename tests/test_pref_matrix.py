"""日程希望 代理入力（医員×日付マトリクス）の保存ロジックの単体テスト。

「-」（未入力）が ○（可能）として保存されないことを検証する。
ネットワーク・secrets 不要でオフライン実行できる。
    .venv/bin/python tests/test_pref_matrix.py
"""
import os
import sys
from datetime import date

# 実データへの書き込みを禁止した状態で import する
os.environ.setdefault("SCHEDULER_READONLY", "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pages.admin_doctor_master import _classify_pref_row, PREF_UNSET

D1, D2, D3 = "2026-09-05", "2026-09-12", "2026-09-19"

_failures = []


def check(label, cond):
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
        _failures.append(label)


def test_untouched_unsubmitted_doctor_is_not_saved():
    """未入力者を触らずに保存しても、○ で登録されない（今回の不具合）"""
    action, payload = _classify_pref_row(1, None, {D1: "-", D2: "-", D3: "-"})
    check("未入力者の全「-」行は保存されない", action == "skip")
    check("payload なし", payload is None)


def test_unset_cells_are_not_saved_as_maru():
    """一部だけ入力した場合、残りの「-」は unset_dates に入り ○ にならない"""
    action, payload = _classify_pref_row(1, None, {D1: "×", D2: "-", D3: "○"})
    check("保存対象になる", action == "save")
    check("× が ng_dates に入る", payload["ng_dates"] == [D1])
    check("「-」が unset_dates に入る", payload["unset_dates"] == [D2])
    check("○ はどのリストにも入らない",
          D3 not in payload["ng_dates"]
          and D3 not in payload["avoid_dates"]
          and D3 not in payload["post_night_dates"]
          and D3 not in payload["unset_dates"])


def test_maru_can_be_reverted_to_unset():
    """既に ○ で登録された日を「-」に戻せる"""
    pref = {"ng_dates": [D1], "avoid_dates": [], "post_night_dates": [],
            "unset_dates": [], "free_text": "", "date_clinic_requests": {},
            "preferred_clinics": []}
    action, payload = _classify_pref_row(1, pref, {D1: "×", D2: "-", D3: "-"})
    check("変更として保存される", action == "save")
    check("「-」にした日が unset_dates へ", set(payload["unset_dates"]) == {D2, D3})
    check("既存の × は維持される", payload["ng_dates"] == [D1])


def test_all_unset_resets_submitted_doctor():
    """全ての日を「-」に戻すと未入力へ（希望レコード削除）"""
    pref = {"ng_dates": [], "avoid_dates": [], "post_night_dates": [],
            "unset_dates": [], "free_text": "", "date_clinic_requests": {},
            "preferred_clinics": []}
    action, payload = _classify_pref_row(1, pref, {D1: "-", D2: "-", D3: "-"})
    check("未入力へ戻す", action == "reset")
    check("payload なし", payload is None)


def test_all_unset_keeps_row_when_other_input_exists():
    """備考など他の入力がある場合は削除せず、日程だけ未入力として残す"""
    pref = {"ng_dates": [D1], "avoid_dates": [], "post_night_dates": [],
            "unset_dates": [], "free_text": "学会のため要相談",
            "date_clinic_requests": {}, "preferred_clinics": []}
    action, payload = _classify_pref_row(1, pref, {D1: "-", D2: "-", D3: "-"})
    check("削除ではなく保存", action == "save")
    check("全日が unset_dates", set(payload["unset_dates"]) == {D1, D2, D3})
    check("ng_dates は空になる", payload["ng_dates"] == [])
    check("備考は維持される", payload["free_text"] == "学会のため要相談")


def test_no_change_is_skipped():
    """編集していない入力済み医員は保存対象にならない"""
    pref = {"ng_dates": [D1], "avoid_dates": [D2], "post_night_dates": [],
            "unset_dates": [D3], "free_text": "", "date_clinic_requests": {},
            "preferred_clinics": []}
    action, _ = _classify_pref_row(1, pref, {D1: "×", D2: "△", D3: "-"})
    check("変更なしはスキップ", action == "skip")


def test_post_night_and_avoid_are_kept():
    """当○ / △ が正しく振り分けられる"""
    action, payload = _classify_pref_row(7, None, {D1: "当○", D2: "△", D3: "○"})
    check("当○ が post_night_dates へ", payload["post_night_dates"] == [D1])
    check("△ が avoid_dates へ", payload["avoid_dates"] == [D2])
    check("doctor_id が入る", payload["doctor_id"] == 7)
    check("保存対象", action == "save")


# ---- 画面（data_editor）経由の検証 ----

# 列ラベルはアプリと同じ書式で生成する（%a はロケール依存のため）
SATURDAYS = [date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19)]
COLS = [s.strftime("%m/%d(%a)") for s in SATURDAYS]


def _matrix_script():
    """スタブDBで代理入力マトリクスだけを描画する AppTest 用スクリプト

    AppTest は別モジュールとして実行するため、この関数内で完結させる。
    """
    import streamlit as st
    import pages.admin_doctor_master as m
    from datetime import date

    saturdays = [date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19)]

    DOCS = [
        {"id": 1, "name": "田中太郎", "job_rank": 1},   # 未入力
        {"id": 2, "name": "鈴木花子", "job_rank": 2},   # 全日○で誤登録済み
    ]
    PREFS = [{"doctor_id": 2, "ng_dates": [], "avoid_dates": [],
              "post_night_dates": [], "unset_dates": [], "preferred_clinics": [],
              "date_clinic_requests": {}, "free_text": "", "updated_at": "x"}]

    st.session_state.setdefault("_calls", [])
    m.get_doctors = lambda *a, **k: DOCS
    m.get_all_preferences = lambda *a, **k: PREFS
    m.batch_upsert_preferences = lambda ym, items: st.session_state["_calls"].append(("upsert", ym, items))
    m.delete_preference = lambda did, ym: st.session_state["_calls"].append(("delete", did, ym))
    m.build_display_name_map = lambda docs: {d["id"]: d["name"] for d in docs}
    m.get_target_saturdays = lambda y, mo: saturdays

    st.session_state["admin_authenticated"] = True
    m._render_pref_matrix("2026-09", 2026, 9)


def _run_matrix(edited_rows=None):
    """マトリクスを描画し「全員を一括保存」を押した後の AppTest を返す

    edited_rows: {行番号: {列ラベル: 値}} data_editor の編集状態を注入する
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_matrix_script)
    at.session_state["schedule_matrix_mode"] = "全員"
    if edited_rows is not None:
        at.session_state["schedule_matrix"] = {
            "edited_rows": edited_rows, "added_rows": [], "deleted_rows": [],
        }
    at.run(timeout=60)
    assert not at.exception, at.exception
    [b for b in at.button if b.label == "全員を一括保存"][0].click().run(timeout=60)
    assert not at.exception, at.exception
    return at


def test_ui_untouched_save_writes_nothing():
    """画面: 何も編集せず一括保存しても書き込みが発生しない"""
    at = _run_matrix()
    check("書き込みなし", at.session_state["_calls"] == [])
    check("メッセージ", at.session_state["_save_msg"] == "変更はありませんでした")


def test_ui_all_unset_deletes_record():
    """画面: 誤登録された医員を全て「-」にすると未入力へ戻る"""
    at = _run_matrix({0: {c: PREF_UNSET for c in COLS}})  # 行0 = 鈴木花子(id=2)
    check("希望レコードを削除", at.session_state["_calls"] == [("delete", 2, "2026-09")])


def test_ui_partial_input_keeps_unset():
    """画面: 1日だけ入力した場合、残りは「-」のまま保存される"""
    at = _run_matrix({1: {COLS[0]: "×"}})  # 行1 = 田中太郎(id=1)
    calls = at.session_state["_calls"]
    check("1件保存", len(calls) == 1 and calls[0][0] == "upsert")
    item = calls[0][2][0]
    check("× が保存される", item["ng_dates"] == ["2026-09-05"])
    check("残りは未入力のまま",
          set(item["unset_dates"]) == {"2026-09-12", "2026-09-19"})


def main():
    tests = [
        test_untouched_unsubmitted_doctor_is_not_saved,
        test_unset_cells_are_not_saved_as_maru,
        test_maru_can_be_reverted_to_unset,
        test_all_unset_resets_submitted_doctor,
        test_all_unset_keeps_row_when_other_input_exists,
        test_no_change_is_skipped,
        test_post_night_and_avoid_are_kept,
        test_ui_untouched_save_writes_nothing,
        test_ui_all_unset_deletes_record,
        test_ui_partial_input_keeps_unset,
    ]
    for t in tests:
        print(f"\n▶ {t.__name__}: {t.__doc__.splitlines()[0]}")
        t()
    print()
    if _failures:
        print(f"❌ {len(_failures)}件失敗")
        sys.exit(1)
    print("✅ 全テスト成功")


if __name__ == "__main__":
    main()
