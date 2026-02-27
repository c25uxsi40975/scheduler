"""スケジュール表の共通表示コンポーネント"""
import streamlit as st
import pandas as pd
from datetime import date


def render_schedule_table(sched, doctors, clinics):
    """スケジュールをカレンダー形式のテーブルで表示する"""
    doc_map = {d["id"]: d["name"] for d in doctors}
    clinic_map = {c["id"]: c["name"] for c in clinics}

    cal_data = {}
    for a in sched["assignments"]:
        ds = a["date"]
        cname = clinic_map.get(a["clinic_id"], "?")
        dname = doc_map.get(a["doctor_id"], "?")
        if ds not in cal_data:
            cal_data[ds] = {}
        cal_data[ds][cname] = dname

    if not cal_data:
        return None

    dates_sorted = sorted(cal_data.keys())
    all_clinic_names = sorted(set(
        cn for day_data in cal_data.values() for cn in day_data.keys()
    ))

    rows = []
    for cn in all_clinic_names:
        row = {"外勤先": cn}
        for ds in dates_sorted:
            d_obj = date.fromisoformat(ds)
            col_name = d_obj.strftime("%m/%d(%a)")
            row[col_name] = cal_data.get(ds, {}).get(cn, "-")
        rows.append(row)

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    return df


def render_doctor_view_table(sched, doctors):
    """医員別ビュー（医員 × 日付 → 外勤先）を表示する"""
    from database import get_clinics

    clinic_map = {c["id"]: c["name"] for c in get_clinics()}

    if not sched["assignments"]:
        return None

    doc_sched = {}
    for a in sched["assignments"]:
        doc_sched.setdefault(a["doctor_id"], {})[a["date"]] = (
            clinic_map.get(a["clinic_id"], "?")
        )

    dates_sorted = sorted(set(a["date"] for a in sched["assignments"]))
    date_labels = {
        ds: date.fromisoformat(ds).strftime("%m/%d(%a)")
        for ds in dates_sorted
    }

    rows = []
    for d in sorted(doctors, key=lambda x: (-x.get("job_rank", 0), x["name"])):
        row = {"医員": d["name"]}
        for ds in dates_sorted:
            row[date_labels[ds]] = doc_sched.get(d["id"], {}).get(ds, "-")
        rows.append(row)

    df = pd.DataFrame(rows)
    st.write("**医員別ビュー:**")
    st.dataframe(df, use_container_width=True, hide_index=True)
    return df


def render_doctor_stats_table(sched, doctors, clinics):
    """医員別統計（今月の回数・報酬 + 累計回数・累計報酬）を表示する"""
    from database import get_all_confirmed_schedules

    if not sched["assignments"]:
        return None

    fee_map = {c["id"]: c.get("fee", 0) for c in clinics}

    # 今月の統計
    doc_stats = {}
    for a in sched["assignments"]:
        did = a["doctor_id"]
        if did not in doc_stats:
            doc_stats[did] = {"回数": 0, "報酬合計": 0}
        doc_stats[did]["回数"] += 1
        doc_stats[did]["報酬合計"] += fee_map.get(a["clinic_id"], 0)

    # 累計（当月含む全確定スケジュール）
    cumulative = {}
    for cs in get_all_confirmed_schedules():
        for a in cs["assignments"]:
            did = a["doctor_id"]
            if did not in cumulative:
                cumulative[did] = {"回数": 0, "報酬合計": 0}
            cumulative[did]["回数"] += 1
            cumulative[did]["報酬合計"] += fee_map.get(a["clinic_id"], 0)

    # 表示中のスケジュールが未確定の場合、その分を累計に加算
    if not sched.get("is_confirmed"):
        for did, s in doc_stats.items():
            if did not in cumulative:
                cumulative[did] = {"回数": 0, "報酬合計": 0}
            cumulative[did]["回数"] += s["回数"]
            cumulative[did]["報酬合計"] += s["報酬合計"]

    rows = []
    for d in sorted(doctors, key=lambda x: (-x.get("job_rank", 0), x["name"])):
        s = doc_stats.get(d["id"], {"回数": 0, "報酬合計": 0})
        c = cumulative.get(d["id"], {"回数": 0, "報酬合計": 0})
        rows.append({
            "医員": d["name"],
            "今月回数": s["回数"],
            "今月報酬": f"¥{s['報酬合計']:,}",
            "累計回数": c["回数"],
            "累計報酬": f"¥{c['報酬合計']:,}",
        })

    df = pd.DataFrame(rows)
    st.write("**医員別統計:**")
    st.dataframe(df, use_container_width=True, hide_index=True)
    return df
