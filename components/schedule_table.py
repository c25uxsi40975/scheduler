"""スケジュール表の共通表示コンポーネント"""
import streamlit as st
import pandas as pd
from datetime import date
from components.display_utils import build_display_name_map


def render_schedule_table(sched, doctors, clinics):
    """スケジュールをカレンダー形式のテーブルで表示する

    休診（枠なし）は赤字「休診」、空き枠（枠あり・未割当）は「空き」で区別表示する。
    sched に year_month があれば必要スロットを算出して判定し、無ければ従来どおり「-」表示。
    """
    doc_map = build_display_name_map(doctors)
    clinic_map = {c["id"]: c["name"] for c in clinics}

    # 割当セル: {date_str: {clinic_id: "医員名（掛け持ちは / 連結）"}}
    cal_data = {}
    for a in sched["assignments"]:
        ds = a["date"]
        cid = a["clinic_id"]
        dname = doc_map.get(a["doctor_id"], "?")
        cell = cal_data.setdefault(ds, {})
        cell[cid] = (cell[cid] + " / " + dname) if cid in cell else dname

    # 必要スロット（休診/空き枠の判定用）。year_month が無ければ休診判定はスキップ
    required_pairs = None
    req_dates = set()
    req_clinic_ids = set()
    year_month = sched.get("year_month")
    if year_month:
        try:
            from optimizer import get_required_slots, get_target_saturdays
            from database import get_clinic_date_overrides
            y, m = map(int, year_month.split("-"))
            sats = get_target_saturdays(y, m)
            ov = get_clinic_date_overrides(year_month)
            req_slots = get_required_slots(clinics, sats, ov)
            required_pairs = {(cid, ds) for cid, ds, _ in req_slots}
            req_dates = {ds for _, ds, _ in req_slots}
            req_clinic_ids = {cid for cid, _, _ in req_slots}
        except Exception:
            required_pairs = None

    if not cal_data and not required_pairs:
        return None

    dates_sorted = sorted(req_dates | set(cal_data.keys()))
    clinic_ids_shown = req_clinic_ids | {a["clinic_id"] for a in sched["assignments"]}
    clinic_ids_sorted = sorted(clinic_ids_shown, key=lambda cid: clinic_map.get(cid, "?"))

    rows = []
    for cid in clinic_ids_sorted:
        row = {"外勤先": clinic_map.get(cid, "?")}
        for ds in dates_sorted:
            col_name = date.fromisoformat(ds).strftime("%m/%d(%a)")
            assigned = cal_data.get(ds, {}).get(cid)
            if assigned:
                row[col_name] = assigned
            elif required_pairs is None:
                row[col_name] = "-"
            elif (cid, ds) in required_pairs:
                row[col_name] = "空き"
            else:
                row[col_name] = "休診"
        rows.append(row)

    df = pd.DataFrame(rows)
    date_cols = [c for c in df.columns if c != "外勤先"]

    def _style_cell(val):
        if val == "休診":
            return "color: #d00000; font-weight: bold"
        if val == "空き":
            return "color: #e08000"
        return ""

    styler = df.style.map(_style_cell, subset=date_cols)
    st.dataframe(styler, use_container_width=True, hide_index=True)
    return df


def render_doctor_view_table(sched, doctors):
    """医員別ビュー（医員 × 日付 → 外勤先）を表示する"""
    from database import get_clinics

    clinic_map = {c["id"]: c["name"] for c in get_clinics()}

    if not sched["assignments"]:
        return None

    display_map = build_display_name_map(doctors)

    doc_sched = {}
    for a in sched["assignments"]:
        did, ds = a["doctor_id"], a["date"]
        cname = clinic_map.get(a["clinic_id"], "?")
        existing = doc_sched.get(did, {}).get(ds)
        if existing:
            # 同一日に複数割り当て（掛け持ち）→ スラッシュ区切り
            doc_sched[did][ds] = existing + " / " + cname
        else:
            doc_sched.setdefault(did, {})[ds] = cname

    dates_sorted = sorted(set(a["date"] for a in sched["assignments"]))
    date_labels = {
        ds: date.fromisoformat(ds).strftime("%m/%d(%a)")
        for ds in dates_sorted
    }

    rows = []
    for d in sorted(doctors, key=lambda x: (x.get("account", ""), x["name"])):
        row = {"医員": display_map.get(d["id"], d["name"])}
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

    display_map = build_display_name_map(doctors)

    rows = []
    for d in sorted(doctors, key=lambda x: (x.get("account", ""), x["name"])):
        s = doc_stats.get(d["id"], {"回数": 0, "報酬合計": 0})
        c = cumulative.get(d["id"], {"回数": 0, "報酬合計": 0})
        rows.append({
            "医員": display_map.get(d["id"], d["name"]),
            "今月回数": s["回数"],
            "今月報酬": f"¥{s['報酬合計']:,}",
            "累計回数": c["回数"],
            "累計報酬": f"¥{c['報酬合計']:,}",
        })

    df = pd.DataFrame(rows)
    st.write("**医員別統計:**")
    st.dataframe(df, use_container_width=True, hide_index=True)
    return df
