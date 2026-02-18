"""管理者: 希望状況一覧タブ"""
import streamlit as st
import pandas as pd
from database import get_doctors, get_clinics, get_all_preferences
from optimizer import get_target_saturdays


def render(target_month, year, month):
    st.header(f"希望状況一覧 ({target_month})")

    doctors = get_doctors()
    clinics = get_clinics()
    clinic_map = {c["id"]: c["name"] for c in clinics}
    prefs = get_all_preferences(target_month)
    pref_map = {p["doctor_id"]: p for p in prefs}

    saturdays = get_target_saturdays(year, month)
    sat_strs = [s.strftime("%m/%d") for s in saturdays]

    if doctors:
        data = []
        for d in doctors:
            p = pref_map.get(d["id"])
            row = {"医員": d["name"], "入力済": "済" if p else "-"}
            if p:
                ng = set(p.get("ng_dates", []))
                avoid = set(p.get("avoid_dates", []))
                dcr = p.get("date_clinic_requests", {})
                for s, s_str in zip(saturdays, sat_strs):
                    ds = s.isoformat()
                    if ds in ng:
                        mark = "×"
                    elif ds in avoid:
                        mark = "△"
                    else:
                        mark = "○"
                    # 日別外勤先希望がある場合は追記
                    if ds in dcr:
                        cid = dcr[ds]
                        if isinstance(cid, str):
                            cid = int(cid) if cid.isdigit() else cid
                        cname = clinic_map.get(cid, "?")
                        mark += f"({cname})"
                    row[s_str] = mark
                row["備考"] = p.get("free_text", "")
            else:
                for s_str in sat_strs:
                    row[s_str] = "-"
                row["備考"] = ""
            data.append(row)

        df = pd.DataFrame(data)
        st.dataframe(df, use_container_width=True, hide_index=True)

        submitted = sum(1 for _ in pref_map.values())
        st.info(f"入力済: {submitted}/{len(doctors)}人")

        # 備考が入力されている医員の詳細表示
        docs_with_notes = [
            (d["name"], pref_map[d["id"]].get("free_text", ""))
            for d in doctors
            if d["id"] in pref_map and pref_map[d["id"]].get("free_text")
        ]
        if docs_with_notes:
            st.subheader("備考一覧")
            for name, text in docs_with_notes:
                st.write(f"**{name}**: {text}")
    else:
        st.warning("医員が登録されていません。マスタ管理で追加してください。")
