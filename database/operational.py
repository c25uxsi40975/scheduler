"""
運用データスプレッドシートの操作
希望・スケジュール・古データ削除
"""
import json
import logging
from datetime import datetime
import streamlit as st

from database.connection import (
    _get_all_records, _find_row_index, _retry, _clear_data_cache,
    _register_cached, _safe_json_loads, _sanitize_cell_value,
    _init_monthly_sheet, _next_id,
    _ws_cache_operational, _get_operational_spreadsheet,
    _OPERATIONAL_PREFIXES,
)
from database.master import get_doctors

_logger = logging.getLogger(__name__)


# ---- Preferences ----

_pref_headers_checked = set()
_pref_header_order = {}  # {year_month: [実際のヘッダー順]}

_PREF_HEADERS = ["doctor_id", "doctor_name", "ng_dates", "avoid_dates",
                 "preferred_clinics", "date_clinic_requests", "free_text", "updated_at",
                 "post_night_dates"]


def _get_pref_sheet(year_month):
    """月別希望シートを取得/作成"""
    name = f"希望_{year_month}"
    ws = _init_monthly_sheet(name, _PREF_HEADERS)
    # 新カラム対応: 既存シートのヘッダー補完（セッション中1回のみ）
    if name not in _pref_headers_checked:
        existing = _retry(ws.row_values, 1)
        if existing:
            missing = [h for h in _PREF_HEADERS if h not in existing]
            if missing:
                actual = existing + missing
                _retry(ws.update, [actual], "A1")
            else:
                actual = existing
        else:
            actual = list(_PREF_HEADERS)
        _pref_header_order[year_month] = actual
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
        r["post_night_dates"] = _safe_json_loads(r.get("post_night_dates"))
        r["free_text"] = str(r.get("free_text", "") or "")
        result.append(r)
    return result


def upsert_preference(doctor_id, year_month, ng_dates=None, avoid_dates=None,
                      preferred_clinics=None, date_clinic_requests=None, free_text=None,
                      post_night_dates=None):
    ws = _get_pref_sheet(year_month)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 医員名を取得（キャッシュ済み）
    doctors = get_doctors(active_only=False)
    doc_name = next((d["name"] for d in doctors if d["id"] == doctor_id), "")

    # 値マップ（ヘッダー名 → 値）
    data_map = {
        "doctor_id": str(doctor_id),
        "doctor_name": doc_name,
        "ng_dates": json.dumps(ng_dates or []),
        "avoid_dates": json.dumps(avoid_dates or []),
        "preferred_clinics": json.dumps(preferred_clinics or []),
        "date_clinic_requests": json.dumps(date_clinic_requests or {}),
        "free_text": _sanitize_cell_value(free_text or ""),
        "updated_at": now,
        "post_night_dates": json.dumps(post_night_dates or []),
    }

    # シートの実際のヘッダー順に合わせてデータを配置
    actual_headers = _pref_header_order.get(year_month, _PREF_HEADERS)
    row_data = [data_map.get(h, "") for h in actual_headers]

    # 既存行を探す
    row_idx = _find_row_index(ws, 1, doctor_id)
    if row_idx:
        _retry(ws.update, [row_data], f"A{row_idx}")
    else:
        _retry(ws.append_row, row_data)
    _clear_data_cache()


def batch_upsert_preferences(year_month, items: list[dict]):
    """複数医員の希望を一括保存（API呼び出し最小化）

    items: [{"doctor_id": int, "ng_dates": [...], "avoid_dates": [...],
             "preferred_clinics": [...], "date_clinic_requests": {...},
             "free_text": str}, ...]
    """
    if not items:
        return
    ws = _get_pref_sheet(year_month)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    actual_headers = _pref_header_order.get(year_month, _PREF_HEADERS)

    # 医員名マップを1回で取得
    doctors = get_doctors(active_only=False)
    name_map = {d["id"]: d["name"] for d in doctors}

    # 既存行のdoctor_id → row_indexマップを1回のAPI呼出で構築
    col_values = _retry(ws.col_values, 1)
    id_to_row = {}
    for i, v in enumerate(col_values):
        if i == 0:
            continue
        if v:
            id_to_row[str(v)] = i + 1

    batch_updates = []
    append_rows = []
    for item in items:
        did = item["doctor_id"]
        data_map = {
            "doctor_id": str(did),
            "doctor_name": name_map.get(did, ""),
            "ng_dates": json.dumps(item.get("ng_dates") or []),
            "avoid_dates": json.dumps(item.get("avoid_dates") or []),
            "preferred_clinics": json.dumps(item.get("preferred_clinics") or []),
            "date_clinic_requests": json.dumps(item.get("date_clinic_requests") or {}),
            "free_text": item.get("free_text") or "",
            "updated_at": now,
            "post_night_dates": json.dumps(item.get("post_night_dates") or []),
        }
        row_data = [data_map.get(h, "") for h in actual_headers]
        row_idx = id_to_row.get(str(did))
        if row_idx:
            n_cols = len(actual_headers)
            col_end = chr(64 + n_cols)
            batch_updates.append({
                'range': f'A{row_idx}:{col_end}{row_idx}',
                'values': [row_data],
            })
        else:
            append_rows.append(row_data)

    if batch_updates:
        _retry(ws.batch_update, batch_updates)
    if append_rows:
        _retry(ws.append_rows, append_rows)
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
            _retry(ws.update, [[
                str(r["id"]), plan_name, json.dumps(assignments),
                total_variance, satisfaction_score, 0, now
            ]], f"A{i+2}")
            _clear_data_cache()
            return

    new_id = _next_id(ws)
    _retry(ws.append_row, [new_id, plan_name, json.dumps(assignments), total_variance, satisfaction_score, 0, now])
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


def _find_schedule_row(schedule_id, year_month=None):
    """schedule_id に対応する (ws, row_index, records) を返すヘルパー。
    year_month 指定時は直接シートを取得し、キャッシュ不整合を回避する。"""
    if year_month:
        ws = _get_sched_sheet(year_month)
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                return ws, i, records
        return None, None, None

    for ws_name, ws in list(_ws_cache_operational.items()):
        if not ws_name.startswith("スケジュール_"):
            continue
        try:
            records = _get_all_records(ws)
        except Exception:
            _logger.warning("削除済みシート '%s' をスキップ", ws_name)
            _ws_cache_operational.pop(ws_name, None)
            continue
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                return ws, i, records
    return None, None, None


def confirm_schedule(schedule_id, year_month=None):
    """スケジュールを確定（バッチ更新で1回のAPI呼出に統合）"""
    ws, idx, records = _find_schedule_row(schedule_id, year_month)
    if ws is None:
        _logger.warning("confirm_schedule: id=%s が見つかりません", schedule_id)
        return
    values = [[0]] * len(records)
    values[idx] = [1]
    _retry(ws.update, values, f"F2:F{len(records)+1}")
    _clear_data_cache()
    _logger.info("スケジュール確定: schedule_id=%s", schedule_id)


def unconfirm_schedule(schedule_id, year_month=None):
    """スケジュールの確定を解除（is_confirmed を 0 に戻す）"""
    ws, idx, _ = _find_schedule_row(schedule_id, year_month)
    if ws is None:
        _logger.warning("unconfirm_schedule: id=%s が見つかりません", schedule_id)
        return
    _retry(ws.update, [[0]], f"F{idx+2}")
    _clear_data_cache()
    _logger.info("スケジュール確定解除: schedule_id=%s", schedule_id)


def delete_schedule(schedule_id, year_month=None):
    ws, idx, _ = _find_schedule_row(schedule_id, year_month)
    if ws is None:
        _logger.warning("delete_schedule: id=%s が見つかりません", schedule_id)
        return
    _retry(ws.delete_rows, idx + 2)
    _clear_data_cache()


def update_schedule_assignments(schedule_id, assignments, year_month=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # year_month が指定されていれば直接シートにアクセス（キャッシュ不整合を回避）
    if year_month:
        ws = _get_sched_sheet(year_month)
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                row_num = i + 2
                assignments_json = json.dumps(assignments, ensure_ascii=False)
                _retry(ws.update, [[assignments_json]], f"C{row_num}")
                _retry(ws.update, [[now]], f"G{row_num}")
                _clear_data_cache()
                _logger.info("下書き保存完了: schedule_id=%s, row=%d", schedule_id, row_num)
                return
        _logger.warning("update_schedule_assignments: id=%s がシート '%s' に見つかりません", schedule_id, f"スケジュール_{year_month}")
        return

    # year_month 未指定時は全シートを検索（後方互換）
    for ws_name, ws in list(_ws_cache_operational.items()):
        if not ws_name.startswith("スケジュール_"):
            continue
        try:
            records = _get_all_records(ws)
        except Exception:
            _logger.warning("削除済みシート '%s' をスキップ", ws_name)
            _ws_cache_operational.pop(ws_name, None)
            continue
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                row_num = i + 2
                assignments_json = json.dumps(assignments, ensure_ascii=False)
                _retry(ws.update, [[assignments_json]], f"C{row_num}")
                _retry(ws.update, [[now]], f"G{row_num}")
                _clear_data_cache()
                _logger.info("下書き保存完了: schedule_id=%s, row=%d", schedule_id, row_num)
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
        try:
            records = _get_all_records(ws)
        except Exception:
            _logger.warning("削除済みシート '%s' をスキップ", ws_name)
            _ws_cache_operational.pop(ws_name, None)
            continue
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
        try:
            records = _get_all_records(ws)
        except Exception:
            _logger.warning("削除済みシート '%s' をスキップ", ws_name)
            _ws_cache_operational.pop(ws_name, None)
            continue
        for r in records:
            if int(r.get("is_confirmed", 0)):
                months.append(year_month)
                break
    months.sort(reverse=True)
    return months


def has_operational_sheets(year_month):
    """指定月の運用シート（希望/スケジュール）がキャッシュに存在するか判定"""
    sched_name = f"スケジュール_{year_month}"
    pref_name = f"希望_{year_month}"
    return sched_name in _ws_cache_operational or pref_name in _ws_cache_operational


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
                    try:
                        _retry(sh_op.del_worksheet, ws)
                    except Exception:
                        _logger.warning("シート '%s' の削除に失敗（既に削除済み?）", ws_name)
                    _ws_cache_operational.pop(ws_name, None)
