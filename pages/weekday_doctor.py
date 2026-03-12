"""
医員の平日セクションビュー
希望入力・スケジュール確認・シフト交換
"""
from datetime import date
from dateutil.relativedelta import relativedelta
import requests
import streamlit as st

from database import (
    get_doctors,
    get_weekday_config_by_section,
    get_active_target_dates,
    get_weekday_preference, upsert_weekday_preference,
    get_weekday_schedule,
    get_weekday_slots,
    get_weekday_open_section, get_weekday_deadline,
    execute_swap, get_swap_history,
)
from components.display_utils import build_display_name_map

DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}


def render(doctor: dict, section: str):
    """医員の平日外勤セクション画面"""
    cfg = get_weekday_config_by_section(section)
    if not cfg:
        st.error("セクション情報が見つかりません")
        return

    clinic_name = cfg["clinic_name"]

    if st.button("← セクション選択に戻る", key=f"back_to_section_{section}"):
        st.session_state.doctor_section = None
        st.rerun()

    st.subheader(clinic_name)

    tab1, tab2, tab3 = st.tabs(["希望入力", "スケジュール確認", "シフト交換"])

    with tab1:
        _render_preference_input(doctor, section, cfg)
    with tab2:
        _render_schedule_view(doctor, section, cfg)
    with tab3:
        _render_shift_swap(doctor, section, cfg)


def _render_preference_input(doctor: dict, section: str, cfg: dict):
    """希望入力タブ"""
    is_open = get_weekday_open_section(section)
    deadline = get_weekday_deadline(section)

    if not is_open:
        st.info("希望入力は現在公開されていません。")
        return

    if deadline:
        try:
            dl_date = date.fromisoformat(deadline)
            if date.today() > dl_date:
                st.warning(f"入力期限（{deadline}）を過ぎています。")
                return
            st.caption(f"入力期限: {deadline}")
        except ValueError:
            pass

    active_dates = get_active_target_dates(section)
    if not active_dates:
        st.info("対象日が設定されていません。")
        return

    pref = get_weekday_preference(doctor["id"], section)
    existing_ng = set(pref.get("ng_dates", []) if pref else [])
    existing_avoid = set(pref.get("avoid_dates", []) if pref else [])
    existing_free = pref.get("free_text", "") if pref else ""

    SCHEDULE_STATUS = ["○", "△", "×"]

    with st.form(f"weekday_pref_{section}_{doctor['id']}"):
        st.write("各日の希望を入力してください（○=可能　△=できれば避けたい　×=NG）")
        n_cols = min(len(active_dates), 5)
        cols = st.columns(n_cols)

        for i, ds in enumerate(active_dates):
            try:
                dt = date.fromisoformat(ds)
                label = dt.strftime("%m/%d(%a)")
            except ValueError:
                label = ds

            if ds in existing_ng:
                default_idx = 2
            elif ds in existing_avoid:
                default_idx = 1
            else:
                default_idx = 0

            with cols[i % n_cols]:
                st.selectbox(
                    label,
                    options=SCHEDULE_STATUS,
                    index=default_idx,
                    key=f"wkpref_{section}_{doctor['id']}_{ds}",
                )

        free_text = st.text_area(
            "備考",
            value=existing_free,
            placeholder="例: 第3週は学会のため不可",
            key=f"wkpref_free_{section}_{doctor['id']}",
        )

        if st.form_submit_button("希望を保存", type="primary"):
            new_ng = []
            new_avoid = []
            for ds in active_dates:
                val = st.session_state.get(f"wkpref_{section}_{doctor['id']}_{ds}", "○")
                if val == "×":
                    new_ng.append(ds)
                elif val == "△":
                    new_avoid.append(ds)

            upsert_weekday_preference(
                doctor["id"], section,
                ng_dates=new_ng,
                avoid_dates=new_avoid,
                free_text=free_text,
            )
            st.success("希望を保存しました")
            st.rerun()


def _render_schedule_view(doctor: dict, section: str, cfg: dict):
    """スケジュール確認タブ"""
    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(-1, 12)]
    view_month = st.selectbox("月を選択", months, key=f"wkdoc_view_month_{section}")

    schedule = get_weekday_schedule(view_month, section)
    if not schedule:
        st.info("この月のスケジュールはまだありません。")
        return

    # 自分の割り当てをハイライト
    my_assignments = [r for r in schedule if r["doctor_id"] == doctor["id"]]
    if my_assignments:
        st.write(f"**あなたの割り当て: {len(my_assignments)}回**")
        for r in my_assignments:
            try:
                dt = date.fromisoformat(r["date"])
                date_label = dt.strftime("%m/%d(%a)")
            except ValueError:
                date_label = r["date"]
            st.write(f"　{date_label}　{r['slot_name']}")
    else:
        st.write("この月の割り当てはありません")

    # 全体スケジュール
    with st.expander("全体スケジュール"):
        # 日付ごとにグループ
        by_date = {}
        for r in schedule:
            if r["date"] not in by_date:
                by_date[r["date"]] = []
            by_date[r["date"]].append(r)

        for ds in sorted(by_date.keys()):
            try:
                dt = date.fromisoformat(ds)
                date_label = dt.strftime("%m/%d(%a)")
            except ValueError:
                date_label = ds
            entries = by_date[ds]
            parts = []
            for r in entries:
                mark = "**" if r["doctor_id"] == doctor["id"] else ""
                parts.append(f"{r['slot_name']}: {mark}{r['doctor_name']}{mark}")
            st.write(f"{date_label}　{' / '.join(parts)}")


def _render_shift_swap(doctor: dict, section: str, cfg: dict):
    """シフト交換タブ"""
    st.write("他のメンバーとシフトを交換できます")

    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(12)]
    swap_month = st.selectbox("月を選択", months, key=f"wkdoc_swap_month_{section}")

    schedule = get_weekday_schedule(swap_month, section)
    if not schedule:
        st.info("この月のスケジュールがありません。")
        return

    my_assignments = [r for r in schedule if r["doctor_id"] == doctor["id"]]
    other_assignments = [r for r in schedule if r["doctor_id"] != doctor["id"]]

    if not my_assignments:
        st.info("この月の自分の割り当てがありません。")
        return

    if not other_assignments:
        st.info("交換可能なシフトがありません。")
        return

    # Step 1: 自分の交換元を選択
    def _my_label(r):
        try:
            dt = date.fromisoformat(r["date"])
            return f"{dt.strftime('%m/%d(%a)')} {r['slot_name']}"
        except ValueError:
            return f"{r['date']} {r['slot_name']}"

    selected_mine = st.selectbox(
        "交換するあなたのシフト",
        my_assignments,
        format_func=_my_label,
        key=f"swap_mine_{section}",
    )

    # Step 2: 交換相手を選択
    def _other_label(r):
        try:
            dt = date.fromisoformat(r["date"])
            return f"{dt.strftime('%m/%d(%a)')} {r['slot_name']} - {r['doctor_name']}"
        except ValueError:
            return f"{r['date']} {r['slot_name']} - {r['doctor_name']}"

    selected_target = st.selectbox(
        "交換先のシフト",
        other_assignments,
        format_func=_other_label,
        key=f"swap_target_{section}",
    )

    if selected_mine and selected_target:
        # 確認ダイアログ
        st.markdown("---")
        st.write("**交換内容の確認**")
        st.write(f"あなた: {_my_label(selected_mine)} → {_other_label(selected_target)}")
        st.write(f"相手: {_other_label(selected_target)} → {_my_label(selected_mine)}")

        if st.button("交換を実行", type="primary", key=f"do_swap_{section}"):
            execute_swap(
                swap_month, section,
                requester_id=doctor["id"],
                original_date=selected_mine["date"],
                original_slot_id=selected_mine["slot_id"],
                target_id=selected_target["doctor_id"],
                target_date=selected_target["date"],
                target_slot_id=selected_target["slot_id"],
            )

            # メール通知（GAS webhook）
            gas_url = st.secrets.get("gas_webapp_url", "")
            if gas_url:
                try:
                    requests.post(gas_url, json={
                        "action": "shift_swap_executed",
                        "section": section,
                        "clinic_name": cfg["clinic_name"],
                        "requester_name": doctor["name"],
                        "requester_shift": _my_label(selected_mine),
                        "target_name": selected_target["doctor_name"],
                        "target_shift": _other_label(selected_target),
                    }, timeout=10)
                except requests.RequestException:
                    pass

            st.success("シフト交換が完了しました")
            st.rerun()

    # 交換履歴
    with st.expander("交換履歴"):
        history = get_swap_history(swap_month, section)
        if history:
            for h in history:
                st.write(
                    f"{h.get('executed_at', '')}　"
                    f"{h.get('requester_name', '')}({h.get('original_date', '')}) ↔ "
                    f"{h.get('target_name', '')}({h.get('target_date', '')})"
                )
        else:
            st.info("交換履歴はありません")
