"""
運用データスプレッドシートの操作
希望・スケジュール・古データ削除
"""
import json
from datetime import datetime
import streamlit as st

from database.connection import (
    _get_all_records, _find_row_index, _retry, _clear_data_cache,
    _register_cached, _safe_json_loads, _init_monthly_sheet, _next_id,
    _ws_cache_operational, _get_operational_spreadsheet,
    _OPERATIONAL_PREFIXES,
)
from database.master import get_doctors


# ---- Preferences ----

_pref_headers_checked = set()


def _get_pref_sheet(year_month):
    """月別希望シートを取得/作成"""
    name = f"希望_{year_month}"
    headers = ["doctor_id", "doctor_name", "ng_dates", "avoid_dates",
               "preferred_clinics", "date_clinic_requests", "free_text", "updated_at"]
    ws = _init_monthly_sheet(name, headers)
    # 新カラム対応: 既存シートのヘッダー補完（セッション中1回のみ）
    if name not in _pref_headers_checked:
        existing = _retry(ws.row_values, 1)
        if existing:
            missing = [h for h in headers if h not in existing]
            if missing:
                ws.update([existing + missing], "A1")
        _pref_headers_checked.add(name)
    return ws


def get_preference(doctor_id, year_month):
    """キャッシュ済みの get_all_preferences から取得（追加API呼び出し不要）"""
    prefs = get_all_preferences(year_month)
    for r in prefs:
        if r["doctor_id"] == doctor_id:
            return r
    return None


@_register_cached
@st.cache_data(ttl=120)
def get_all_preferences(year_month):
    ws = _get_pref_sheet(year_month)
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["doctor_id"] = int(r["doctor_id"])
        r["ng_dates"] = _safe_json_loads(r.get("ng_dates"))
        r["avoid_dates"] = _safe_json_loads(r.get("avoid_dates"))
        r["preferred_clinics"] = _safe_json_loads(r.get("preferred_clinics"))
        r["date_clinic_requests"] = _safe_json_loads(r.get("date_clinic_requests"), default={})
        r["free_text"] = str(r.get("free_text", "") or "")
        result.append(r)
    return result


def upsert_preference(doctor_id, year_month, ng_dates=None, avoid_dates=None,
                      preferred_clinics=None, date_clinic_requests=None, free_text=None):
    ws = _get_pref_sheet(year_month)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ng = json.dumps(ng_dates or [])
    av = json.dumps(avoid_dates or [])
    pc = json.dumps(preferred_clinics or [])
    dcr = json.dumps(date_clinic_requests or {})
    ft = free_text or ""

    # 医員名を取得（キャッシュ済み）
    doctors = get_doctors(active_only=False)
    doc_name = ""
    for d in doctors:
        if d["id"] == doctor_id:
            doc_name = d["name"]
            break

    row_data = [str(doctor_id), doc_name, ng, av, pc, dcr, ft, now]

    # 既存行を探す
    row_idx = _find_row_index(ws, 1, doctor_id)
    if row_idx:
        ws.update([row_data], f"A{row_idx}")
    else:
        ws.append_row(row_data)
    _clear_data_cache()


# ---- Schedules ----

def _get_sched_sheet(year_month):
    """月別スケジュールシートを取得/作成"""
    name = f"スケジュール_{year_month}"
    headers = ["id", "plan_name", "assignments", "total_variance", "satisfaction_score", "is_confirmed", "created_at"]
    return _init_monthly_sheet(name, headers)


def save_schedule(year_month, plan_name, assignments, total_variance=0, satisfaction_score=0):
    ws = _get_sched_sheet(year_month)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = _get_all_records(ws)

    # 同名プランがあれば更新
    for i, r in enumerate(records):
        if r.get("plan_name") == plan_name:
            ws.update([[
                str(r["id"]), plan_name, json.dumps(assignments),
                total_variance, satisfaction_score, 0, now
            ]], f"A{i+2}")
            _clear_data_cache()
            return

    new_id = _next_id(ws)
    ws.append_row([new_id, plan_name, json.dumps(assignments), total_variance, satisfaction_score, 0, now])
    _clear_data_cache()


@_register_cached
@st.cache_data(ttl=120)
def get_schedules(year_month):
    ws = _get_sched_sheet(year_month)
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = int(r["id"])
        r["year_month"] = year_month
        r["total_variance"] = float(r.get("total_variance", 0))
        r["satisfaction_score"] = float(r.get("satisfaction_score", 0))
        r["is_confirmed"] = int(r.get("is_confirmed", 0))
        r["assignments"] = _safe_json_loads(r.get("assignments"))
        result.append(r)
    return result


def confirm_schedule(schedule_id):
    """スケジュールを確定（バッチ更新で1回のAPI呼出に統合）"""
    for ws_name, ws in list(_ws_cache_operational.items()):
        if not ws_name.startswith("スケジュール_"):
            continue
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                # 全行の is_confirmed を一括更新（ループ update_cell → 1回の batch update）
                values = [[0]] * len(records)
                values[i] = [1]
                _retry(ws.update, values, f"F2:F{len(records)+1}")
                _clear_data_cache()
                return


def delete_schedule(schedule_id):
    for ws_name, ws in list(_ws_cache_operational.items()):
        if not ws_name.startswith("スケジュール_"):
            continue
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                ws.delete_rows(i + 2)
                _clear_data_cache()
                return


def update_schedule_assignments(schedule_id, assignments):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for ws_name, ws in list(_ws_cache_operational.items()):
        if not ws_name.startswith("スケジュール_"):
            continue
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                row_num = i + 2
                # 2つのセルを1回のbatch_updateで更新（C列=assignments, G列=updated_at）
                _retry(ws.batch_update, [
                    {'range': f'C{row_num}', 'values': [[json.dumps(assignments)]]},
                    {'range': f'G{row_num}', 'values': [[now]]},
                ])
                _clear_data_cache()
                return


@_register_cached
@st.cache_data(ttl=120)
def get_all_confirmed_schedules():
    """全月の確定スケジュールを取得（累計報酬計算用）"""
    result = []
    for ws_name, ws in list(_ws_cache_operational.items()):
        if not ws_name.startswith("スケジュール_"):
            continue
        year_month = ws_name.replace("スケジュール_", "")
        records = _get_all_records(ws)
        for r in records:
            if int(r.get("is_confirmed", 0)):
                r["id"] = int(r["id"])
                r["year_month"] = year_month
                r["assignments"] = _safe_json_loads(r.get("assignments"))
                result.append(r)
    result.sort(key=lambda x: x.get("year_month", ""))
    return result


@_register_cached
@st.cache_data(ttl=120)
def get_confirmed_months():
    """確定済みスケジュールが存在する月のリストを返す"""
    months = []
    for ws_name, ws in list(_ws_cache_operational.items()):
        if not ws_name.startswith("スケジュール_"):
            continue
        year_month = ws_name.replace("スケジュール_", "")
        records = _get_all_records(ws)
        for r in records:
            if int(r.get("is_confirmed", 0)):
                months.append(year_month)
                break
    months.sort(reverse=True)
    return months


# ---- Cleanup ----

def delete_old_schedules(months_to_keep=4):
    """古い月別シートを削除（キャッシュ使用 -- worksheets() API不要）"""
    from dateutil.relativedelta import relativedelta
    cutoff = (datetime.now() - relativedelta(months=months_to_keep)).strftime("%Y-%m")
    sh_op = _get_operational_spreadsheet()
    for ws_name, ws in list(_ws_cache_operational.items()):
        for prefix in _OPERATIONAL_PREFIXES:
            if ws_name.startswith(prefix):
                ym = ws_name.replace(prefix, "")
                if ym < cutoff:
                    _retry(sh_op.del_worksheet, ws)
                    _ws_cache_operational.pop(ws_name, None)
