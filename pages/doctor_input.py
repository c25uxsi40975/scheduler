"""医員: 希望入力タブ"""
import streamlit as st
import requests
from database import get_preference, upsert_preference
from optimizer import get_target_saturdays


def _send_preference_notification(doctor_name, target_month):
    """GAS Web App経由で希望入力通知メールを管理者に送信"""
    gas_url = st.secrets.get("gas_webapp_url", "")
    if not gas_url:
        return
    try:
        requests.post(gas_url, json={
            "action": "preference_submitted",
            "year_month": target_month,
            "doctor_name": doctor_name,
        }, timeout=10)
    except requests.RequestException:
        pass  # 通知失敗は医員側に表示しない


DAY_STATUS_OPTIONS = ["○ 可能", "△ できれば避けたい", "× NG"]


def render(doctor, target_month, year, month):
    st.header(f"希望入力 ({target_month})")
    st.write(f"**{doctor['name']}** さんの希望を入力してください")

    saturdays = get_target_saturdays(year, month)
    existing = get_preference(doctor["id"], target_month)
    existing_ng = set(existing["ng_dates"]) if existing else set()
    existing_avoid = set(existing["avoid_dates"]) if existing else set()

    # 日程の希望入力（○△×）
    st.subheader("日程の希望")
    st.caption("○ 出勤可能 ／ △ できれば避けたい ／ × NG（出勤不可）")

    ng_dates = []
    avoid_dates = []
    cols = st.columns(min(len(saturdays), 5)) if saturdays else []
    for i, s in enumerate(saturdays):
        ds = s.isoformat()
        with cols[i % len(cols)]:
            if ds in existing_ng:
                default_idx = 2
            elif ds in existing_avoid:
                default_idx = 1
            else:
                default_idx = 0
            status = st.radio(
                s.strftime("%m/%d(%a)"),
                DAY_STATUS_OPTIONS,
                index=default_idx,
                key=f"day_{ds}",
            )
            if status == "× NG":
                ng_dates.append(ds)
            elif status == "△ できれば避けたい":
                avoid_dates.append(ds)

    # 自由入力欄
    st.subheader("自由入力欄")
    st.caption("スケジュールに関する希望や備考があれば自由にご記入ください")
    existing_free_text = existing.get("free_text", "") if existing else ""
    free_text = st.text_area(
        "自由入力",
        value=existing_free_text,
        placeholder="例: 3月は学会のため第2週は避けたいです",
        key="free_text_input",
        label_visibility="collapsed",
    )

    if st.button("保存", type="primary", use_container_width=True):
        # 管理者が設定した日別外勤先希望を保持
        existing_dcr = existing.get("date_clinic_requests", {}) if existing else {}
        existing_pref_clinics = existing.get("preferred_clinics", []) if existing else []
        upsert_preference(
            doctor["id"], target_month,
            ng_dates=ng_dates,
            avoid_dates=avoid_dates,
            preferred_clinics=existing_pref_clinics,
            date_clinic_requests=existing_dcr,
            free_text=free_text,
        )
        _send_preference_notification(doctor["name"], target_month)
        st.session_state["_doc_saved"] = True
        st.rerun()

    # ---- 保存済み内容の表示 ----
    if existing:
        if st.session_state.pop("_doc_saved", False):
            st.success("保存しました！")

        st.markdown("---")
        st.subheader("現在の入力内容")
        st.caption(f"最終更新: {existing['updated_at']}")

        # 日程サマリー
        sat_strs = []
        for s in saturdays:
            ds = s.isoformat()
            label = s.strftime("%m/%d")
            if ds in existing_ng:
                sat_strs.append(f"**{label}** × NG")
            elif ds in existing_avoid:
                sat_strs.append(f"**{label}** △")
            else:
                sat_strs.append(f"**{label}** ○")
        st.write("　".join(sat_strs))

        # 備考
        ft = existing.get("free_text", "")
        if ft:
            st.write(f"備考: {ft}")
