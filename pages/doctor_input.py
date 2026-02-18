"""医員: 希望入力タブ"""
import streamlit as st
from database import get_clinics, get_preference, upsert_preference
from optimizer import get_target_saturdays


DAY_STATUS_OPTIONS = ["○ 可能", "△ できれば避けたい", "× NG"]


def render(doctor, target_month, year, month):
    st.header(f"希望入力 ({target_month})")
    st.write(f"**{doctor['name']}** さんの希望を入力してください")

    saturdays = get_target_saturdays(year, month)
    clinics = get_clinics()

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

    # 日別外勤先希望
    st.subheader("日別外勤先希望")
    st.caption("特定の日に行きたい外勤先がある場合に選択してください（任意）")

    existing_dcr = existing.get("date_clinic_requests", {}) if existing else {}
    date_clinic_requests = {}

    clinic_options = [0] + [c["id"] for c in clinics]

    def _clinic_label(cid):
        if cid == 0:
            return "指定なし"
        return next((c["name"] for c in clinics if c["id"] == cid), str(cid))

    dcr_cols = st.columns(min(len(saturdays), 5)) if saturdays else []
    for i, s in enumerate(saturdays):
        ds = s.isoformat()
        with dcr_cols[i % len(dcr_cols)]:
            if ds in ng_dates:
                st.caption(s.strftime("%m/%d") + " ×")
                continue
            existing_cid = existing_dcr.get(ds, 0)
            if isinstance(existing_cid, str):
                existing_cid = int(existing_cid) if existing_cid.isdigit() else 0
            default_idx = clinic_options.index(existing_cid) if existing_cid in clinic_options else 0
            selected_cid = st.selectbox(
                s.strftime("%m/%d(%a)"),
                clinic_options,
                index=default_idx,
                format_func=_clinic_label,
                key=f"dcr_{ds}",
            )
            if selected_cid != 0:
                date_clinic_requests[ds] = selected_cid

    # 希望外勤先
    st.subheader("希望外勤先（行きたい外勤先）")
    pref_clinics = st.multiselect(
        "希望する外勤先を選択",
        [c["id"] for c in clinics],
        default=(existing["preferred_clinics"] if existing else []),
        format_func=lambda cid: next(
            (f"{c['name']} (¥{c['fee']:,})" for c in clinics if c["id"] == cid), str(cid)
        ),
        label_visibility="collapsed"
    )

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
        upsert_preference(
            doctor["id"], target_month,
            ng_dates=ng_dates,
            avoid_dates=avoid_dates,
            preferred_clinics=pref_clinics,
            date_clinic_requests=date_clinic_requests,
            free_text=free_text,
        )
        st.success("保存しました！")
        st.rerun()

    if existing:
        st.info(f"最終更新: {existing['updated_at']}")
