"""
医員: 土曜シフト変更タブ
指定日のシフトを別の医員に差し替える（一方向）
"""
from datetime import date
import requests
import streamlit as st

from audit import log_event
from database import (
    get_clinics, get_doctors,
    get_schedules, get_confirmed_months,
    get_all_preferences,
    execute_saturday_shift_change, get_saturday_shift_change_history,
)

DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}


def render(doctor: dict):
    """土曜シフト変更タブ — 指定日のシフトを別の医員に差し替える"""
    st.write("確定済みの土曜シフトを別の医員に差し替えできます")
    st.caption("交換（双方向）ではなく、1人を別の医員に置き換える一方向の更新です。")

    confirmed_months = get_confirmed_months()
    if not confirmed_months:
        st.info("確定済みのスケジュールがありません。")
        return

    change_month = st.selectbox("月を選択", confirmed_months, key="sat_change_month")

    schedules = get_schedules(change_month)
    confirmed = [s for s in schedules if s["is_confirmed"]]
    if not confirmed:
        st.info("この月の確定スケジュールがありません。")
        return

    sched = confirmed[0]
    schedule_id = sched["id"]
    assignments = sched["assignments"]

    if not assignments:
        st.info("変更可能なシフトがありません。")
        return

    # 外勤先・医員マップ
    clinics = get_clinics()
    clinic_map = {c["id"]: c["name"] for c in clinics}
    all_doctors = get_doctors(active_only=False)
    doc_map = {d["id"]: d["name"] for d in all_doctors}

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
    rows.sort(key=lambda r: (r["date"], r["clinic_name"]))

    # NG/△の事前計算
    prefs = get_all_preferences(change_month)
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

    def _shift_label(r):
        base = f"{r['clinic_name']} - {r['doctor_name']}"
        key = (r["doctor_id"], r["date"])
        if key in ng_set:
            return f"⛔ {base}【NG】"
        if key in avoid_set:
            return f"⚠ {base}【△】"
        return base

    # Step 1: 日付選択
    dates = sorted({r["date"] for r in rows})
    selected_date = st.selectbox(
        "日付を選択",
        dates,
        format_func=_date_label,
        key="sat_change_date",
    )
    if not selected_date:
        return

    # Step 2: 変更対象シフトを選択
    rows_on_date = [r for r in rows if r["date"] == selected_date]
    if not rows_on_date:
        st.info("この日のシフトがありません。")
        return

    selected_src = st.selectbox(
        "変更対象のシフト（変更元）",
        rows_on_date,
        format_func=_shift_label,
        key="sat_change_src",
    )
    if not selected_src:
        return

    # Step 3: 変更後の医員を選択（同日に既割当の医員は除外）
    occupied_ids = {r["doctor_id"] for r in rows_on_date}
    candidate_doctors = [
        d for d in all_doctors
        if d.get("is_active", True) and d["id"] not in occupied_ids
    ]
    if not candidate_doctors:
        st.info("差し替え可能な医員がいません（同日に他の外勤がある医員は除外されます）。")
        return

    def _doc_label(d):
        ds = selected_src["date"]
        name = d["name"]
        if (d["id"], ds) in ng_set:
            return f"⛔ {name}【NG】"
        if (d["id"], ds) in avoid_set:
            return f"⚠ {name}【△】"
        return name

    new_doctor = st.selectbox(
        "変更後の医員（変更先）",
        candidate_doctors,
        format_func=_doc_label,
        key="sat_change_dst",
    )
    if not new_doctor:
        return

    new_doctor_id = new_doctor["id"]
    new_name = new_doctor["name"]

    st.markdown("---")
    st.write("**変更内容の確認**")
    st.write(f"操作者: {doctor['name']}")
    st.write(
        f"{_date_label(selected_src['date'])} {selected_src['clinic_name']}: "
        f"{selected_src['doctor_name']} → {new_name}"
    )

    if (new_doctor_id, selected_src["date"]) in ng_set:
        st.warning(f"⛔ {new_name}先生はこの日をNGに設定しています。")
    elif (new_doctor_id, selected_src["date"]) in avoid_set:
        st.info(f"⚠ {new_name}先生はこの日を△（避けたい）に設定しています。")

    if st.button("変更を実行", type="primary", key="sat_do_change"):
        try:
            execute_saturday_shift_change(
                year_month=change_month,
                schedule_id=schedule_id,
                change_date=selected_src["date"],
                clinic_id=selected_src["clinic_id"],
                original_doctor_id=selected_src["doctor_id"],
                new_doctor_id=new_doctor_id,
                actor_id=doctor["id"],
            )
        except ValueError as e:
            st.error(str(e))
            return

        # 監査ログ
        log_event(
            "saturday_shift_change",
            actor=doctor["name"],
            detail=(
                f"土曜 {change_month} {selected_src['date']} "
                f"{selected_src['clinic_name']}: "
                f"{selected_src['doctor_name']} → {new_name}"
            ),
        )

        # 通知（GAS webhook）— 元医員・新医員・管理者へ
        gas_url = st.secrets.get("gas_webapp_url", "")
        if gas_url:
            original_doctor = next(
                (d for d in all_doctors if d["id"] == selected_src["doctor_id"]),
                {},
            )
            try:
                requests.post(gas_url, json={
                    "action": "saturday_shift_change_executed",
                    "year_month": change_month,
                    "date": selected_src["date"],
                    "clinic_id": selected_src["clinic_id"],
                    "clinic_name": selected_src["clinic_name"],
                    "actor_id": doctor["id"],
                    "actor_name": doctor["name"],
                    "original_doctor_id": selected_src["doctor_id"],
                    "original_doctor_name": selected_src["doctor_name"],
                    "original_doctor_email": original_doctor.get("email", ""),
                    "new_doctor_id": new_doctor_id,
                    "new_doctor_name": new_name,
                    "new_doctor_email": new_doctor.get("email", ""),
                }, timeout=10)
            except requests.RequestException:
                pass

        st.success("シフト変更が完了しました")
        st.rerun()

    # 変更履歴
    with st.expander("変更履歴"):
        history = get_saturday_shift_change_history(change_month)
        if history:
            for h in sorted(history, key=lambda x: x.get("executed_at", ""), reverse=True):
                actor = h.get("actor_name", "")
                actor_info = f"[{actor}] " if actor else ""
                st.write(
                    f"{h.get('executed_at', '')}　{actor_info}"
                    f"{h.get('date', '')} {h.get('clinic_name', '')}: "
                    f"{h.get('original_doctor_name', '')} → "
                    f"{h.get('new_doctor_name', '')}"
                )
        else:
            st.info("変更履歴はありません")
