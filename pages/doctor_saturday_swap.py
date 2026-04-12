"""
医員: 土曜シフト交換タブ
同日の別医員と外勤先を入れ替える
"""
from datetime import date
import requests
import streamlit as st

from audit import log_event
from database import (
    get_clinics,
    get_schedules, get_confirmed_months,
    get_all_preferences,
    execute_saturday_swap, get_saturday_swap_history,
)

DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}


def render(doctor: dict):
    """土曜シフト交換タブ — 同日の医員同士で外勤先を交換"""
    st.write("同じ土曜日に入っている医員同士で外勤先を交換できます")

    confirmed_months = get_confirmed_months()
    if not confirmed_months:
        st.info("確定済みのスケジュールがありません。")
        return

    swap_month = st.selectbox("月を選択", confirmed_months, key="sat_swap_month")

    schedules = get_schedules(swap_month)
    confirmed = [s for s in schedules if s["is_confirmed"]]
    if not confirmed:
        st.info("この月の確定スケジュールがありません。")
        return

    sched = confirmed[0]
    schedule_id = sched["id"]
    assignments = sched["assignments"]

    if len(assignments) < 2:
        st.info("交換可能なシフトがありません。")
        return

    # 外勤先マップ
    clinics = get_clinics()
    clinic_map = {c["id"]: c["name"] for c in clinics}

    # 医員名を補完（assignments には doctor_name が含まれないので get_doctors を使う）
    from database import get_doctors
    doctors = get_doctors(active_only=False)
    doc_map = {d["id"]: d["name"] for d in doctors}

    # 割当にラベル用情報を付与
    rows = []
    for a in assignments:
        rows.append({
            "date": str(a.get("date")),
            "clinic_id": int(a.get("clinic_id", 0)),
            "clinic_name": clinic_map.get(int(a.get("clinic_id", 0)), "?"),
            "doctor_id": int(a.get("doctor_id", 0)),
            "doctor_name": doc_map.get(int(a.get("doctor_id", 0)), "?"),
        })

    # 日付順 → 外勤先名順でソート
    rows.sort(key=lambda r: (r["date"], r["clinic_name"]))

    # NG/△の事前計算
    prefs = get_all_preferences(swap_month)
    ng_set = set()
    avoid_set = set()
    for pref in prefs:
        did = pref.get("doctor_id")
        for ds in (pref.get("ng_dates") or []):
            ng_set.add((did, ds))
        for ds in (pref.get("avoid_dates") or []):
            avoid_set.add((did, ds))

    def _date_label(ds):
        try:
            d = date.fromisoformat(ds)
            return f"{d.strftime('%m/%d')}({DAY_NAMES[d.weekday()]})"
        except ValueError:
            return ds

    def _source_label(r):
        base = f"{_date_label(r['date'])} {r['clinic_name']} - {r['doctor_name']}"
        key = (r["doctor_id"], r["date"])
        if key in ng_set:
            return f"⛔ {base}【NG】"
        if key in avoid_set:
            return f"⚠ {base}【△】"
        return base

    def _target_label(r):
        base = f"{r['clinic_name']} - {r['doctor_name']}"
        key = (r["doctor_id"], r["date"])
        if key in ng_set:
            return f"⛔ {base}【NG】"
        if key in avoid_set:
            return f"⚠ {base}【△】"
        return base

    # Step 1: 交換元のシフトを選択（全割当対象）
    selected_a = st.selectbox(
        "交換元のシフト",
        rows,
        format_func=_source_label,
        key="sat_swap_a",
    )

    # Step 2: 交換先（同日・別医員のみ）
    if not selected_a:
        return

    candidates = [
        r for r in rows
        if r["doctor_id"] != selected_a["doctor_id"]
        and r["date"] == selected_a["date"]
    ]
    if not candidates:
        st.info("同じ日付に他の医員がいません。")
        return

    selected_b = st.selectbox(
        "交換先の医員（同日の別医員）",
        candidates,
        format_func=_target_label,
        key="sat_swap_b",
    )

    if not selected_b:
        return

    st.markdown("---")
    st.write("**交換内容の確認**")
    st.write(f"操作者: {doctor['name']}")
    st.write(
        f"{_date_label(selected_a['date'])}: "
        f"{selected_a['doctor_name']}({selected_a['clinic_name']}) ↔ "
        f"{selected_b['doctor_name']}({selected_b['clinic_name']})"
    )

    if st.button("交換を実行", type="primary", key="sat_do_swap"):
        execute_saturday_swap(
            year_month=swap_month,
            schedule_id=schedule_id,
            swap_date=selected_a["date"],
            requester_id=selected_a["doctor_id"],
            requester_clinic_id=selected_a["clinic_id"],
            target_id=selected_b["doctor_id"],
            target_clinic_id=selected_b["clinic_id"],
            actor_id=doctor["id"],
        )

        # 監査ログ
        log_event(
            "saturday_shift_swap",
            actor=doctor["name"],
            detail=(
                f"土曜 {swap_month} {selected_a['date']}: "
                f"{selected_a['doctor_name']}({selected_a['clinic_name']}) ↔ "
                f"{selected_b['doctor_name']}({selected_b['clinic_name']})"
            ),
        )

        # 通知（GAS webhook）
        gas_url = st.secrets.get("gas_webapp_url", "")
        if gas_url:
            try:
                requests.post(gas_url, json={
                    "action": "saturday_shift_swap_executed",
                    "year_month": swap_month,
                    "swap_date": selected_a["date"],
                    "actor_name": doctor["name"],
                    "actor_id": doctor["id"],
                    "requester_id": selected_a["doctor_id"],
                    "requester_name": selected_a["doctor_name"],
                    "requester_shift": f"{selected_a['date']} {selected_a['clinic_name']} - {selected_a['doctor_name']}",
                    "target_id": selected_b["doctor_id"],
                    "target_name": selected_b["doctor_name"],
                    "target_shift": f"{selected_b['date']} {selected_b['clinic_name']} - {selected_b['doctor_name']}",
                    "requester_clinic_id": selected_a["clinic_id"],
                    "target_clinic_id": selected_b["clinic_id"],
                }, timeout=10)
            except requests.RequestException:
                pass

        st.success("シフト交換が完了しました")
        st.rerun()

    # 交換履歴
    with st.expander("交換履歴"):
        history = get_saturday_swap_history(swap_month)
        if history:
            for h in history:
                actor = h.get("actor_name", "")
                actor_info = f"[{actor}] " if actor else ""
                st.write(
                    f"{h.get('executed_at', '')}　{actor_info}"
                    f"{h.get('requester_name', '')}({h.get('original_clinic_name', '')}) ↔ "
                    f"{h.get('target_name', '')}({h.get('target_clinic_name', '')}) "
                    f"@ {h.get('original_date', '')}"
                )
        else:
            st.info("交換履歴はありません")
