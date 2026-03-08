"""
平日外勤のデータ操作
セクション設定・スロット・対象日・希望・スケジュール・シフト交換
"""
import json
from datetime import datetime
import streamlit as st

from database.connection import (
    _get_sheet, _get_all_records, _find_row_index, _next_id,
    _col_letter, _retry, _clear_data_cache, _register_cached,
    _safe_json_loads, _sanitize_cell_value,
    _init_monthly_sheet, _ws_cache_operational,
)
from database.master import get_doctors


def _safe_int(val, default=0):
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        val = val.strip()
        if val.upper() == "TRUE":
            return 1
        if val.upper() == "FALSE":
            return 0
        if val == "":
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default
    return default


# ---- セクション設定 CRUD ----

@_register_cached
@st.cache_data(ttl=120)
def get_weekday_configs():
    """平日外勤設定の全レコードを取得"""
    ws = _get_sheet("平日外勤設定")
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = _safe_int(r["id"])
        r["section"] = str(r.get("section", ""))
        r["clinic_name"] = str(r.get("clinic_name", ""))
        r["days_of_week"] = _safe_json_loads(r.get("days_of_week", "[]"))
        r["assigned_doctors"] = _safe_json_loads(r.get("assigned_doctors", "[]"))
        r["is_active"] = _safe_int(r.get("is_active", 1), default=1)
        result.append(r)
    return result


def get_weekday_config_by_section(section: str):
    """セクションキーで設定を取得"""
    configs = get_weekday_configs()
    for c in configs:
        if c["section"] == section:
            return c
    return None


def add_weekday_config(clinic_name: str, days_of_week: list[int], assigned_doctors: list[int] = None):
    """平日外勤セクションを追加"""
    ws = _get_sheet("平日外勤設定")
    records = _get_all_records(ws)
    # section キーを自動生成
    existing_sections = {r.get("section", "") for r in records}
    idx = 1
    while f"weekday_{idx}" in existing_sections:
        idx += 1
    section = f"weekday_{idx}"

    new_id = _next_id(ws)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    actual_headers = _retry(ws.row_values, 1)
    values = {
        "id": new_id,
        "section": section,
        "clinic_name": _sanitize_cell_value(clinic_name),
        "days_of_week": json.dumps(days_of_week),
        "assigned_doctors": json.dumps(assigned_doctors or []),
        "is_active": 1,
        "created_at": now,
    }
    row = [values.get(h, "") for h in actual_headers]
    ws.append_row(row)
    _clear_data_cache()
    return section


def update_weekday_config(section: str, **kwargs):
    """平日外勤設定を更新"""
    ws = _get_sheet("平日外勤設定")
    records = _get_all_records(ws)
    row_idx = None
    for i, r in enumerate(records):
        if r.get("section") == section:
            row_idx = i + 2
            break
    if not row_idx:
        return
    actual_headers = _retry(ws.row_values, 1)
    updates = []
    for key, val in kwargs.items():
        if key in ("days_of_week", "assigned_doctors"):
            val = json.dumps(val)
        if key == "clinic_name":
            val = _sanitize_cell_value(val)
        if key in actual_headers:
            col = actual_headers.index(key) + 1
            updates.append({"range": f"{_col_letter(col)}{row_idx}", "values": [[val]]})
    if updates:
        _retry(ws.batch_update, updates)
    _clear_data_cache()


def delete_weekday_config(section: str):
    """平日外勤セクションを削除"""
    ws = _get_sheet("平日外勤設定")
    records = _get_all_records(ws)
    for i, r in enumerate(records):
        if r.get("section") == section:
            ws.delete_rows(i + 2)
            break
    _clear_data_cache()


# ---- スロットマスタ CRUD ----

@_register_cached
@st.cache_data(ttl=120)
def get_weekday_slots(section: str = None):
    """平日スロットマスタを取得（section指定でフィルタ可能）"""
    ws = _get_sheet("平日スロットマスタ")
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = _safe_int(r["id"])
        r["section"] = str(r.get("section", ""))
        r["slot_name"] = str(r.get("slot_name", ""))
        r["day_of_week"] = _safe_int(r.get("day_of_week", 0))
        r["start_time"] = str(r.get("start_time", ""))
        r["end_time"] = str(r.get("end_time", ""))
        r["required_count"] = _safe_int(r.get("required_count", 1), default=1)
        r["is_active"] = _safe_int(r.get("is_active", 1), default=1)
        if section and r["section"] != section:
            continue
        result.append(r)
    return result


def add_weekday_slot(section: str, slot_name: str, day_of_week: int,
                     start_time: str, end_time: str, required_count: int = 1):
    """平日スロットを追加"""
    ws = _get_sheet("平日スロットマスタ")
    new_id = _next_id(ws)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    actual_headers = _retry(ws.row_values, 1)
    values = {
        "id": new_id,
        "section": section,
        "slot_name": _sanitize_cell_value(slot_name),
        "day_of_week": day_of_week,
        "start_time": start_time,
        "end_time": end_time,
        "required_count": required_count,
        "is_active": 1,
        "created_at": now,
    }
    row = [values.get(h, "") for h in actual_headers]
    ws.append_row(row)
    _clear_data_cache()
    return new_id


def update_weekday_slot(slot_id: int, **kwargs):
    """平日スロットを更新"""
    ws = _get_sheet("平日スロットマスタ")
    row_idx = _find_row_index(ws, 1, slot_id)
    if not row_idx:
        return
    actual_headers = _retry(ws.row_values, 1)
    updates = []
    for key, val in kwargs.items():
        if key == "slot_name":
            val = _sanitize_cell_value(val)
        if key in actual_headers:
            col = actual_headers.index(key) + 1
            updates.append({"range": f"{_col_letter(col)}{row_idx}", "values": [[val]]})
    if updates:
        _retry(ws.batch_update, updates)
    _clear_data_cache()


def delete_weekday_slot(slot_id: int):
    """平日スロットを削除"""
    ws = _get_sheet("平日スロットマスタ")
    row_idx = _find_row_index(ws, 1, slot_id)
    if row_idx:
        ws.delete_rows(row_idx)
    _clear_data_cache()


# ---- スケジュール対象日 CRUD ----

@_register_cached
@st.cache_data(ttl=120)
def get_target_dates(section: str):
    """指定セクションのスケジュール対象日を取得"""
    ws = _get_sheet("スケジュール対象日")
    records = _get_all_records(ws)
    result = []
    for r in records:
        if str(r.get("section", "")) != section:
            continue
        r["id"] = _safe_int(r["id"])
        r["section"] = str(r.get("section", ""))
        r["date"] = str(r.get("date", ""))
        r["is_active"] = _safe_int(r.get("is_active", 1), default=1)
        result.append(r)
    result.sort(key=lambda x: x["date"])
    return result


def get_active_target_dates(section: str) -> list[str]:
    """有効な対象日の日付文字列リストを返す"""
    all_dates = get_target_dates(section)
    return [d["date"] for d in all_dates if d["is_active"]]


def set_target_dates(section: str, dates: list[str], active_dates: list[str] = None):
    """対象日を一括設定（既存をクリアして再作成）

    Args:
        section: セクションキー
        dates: 全日付リスト
        active_dates: 有効な日付リスト（Noneの場合、全て有効）
    """
    ws = _get_sheet("スケジュール対象日")
    records = _get_all_records(ws)
    active_set = set(active_dates) if active_dates is not None else set(dates)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 既存の当該セクション行を削除（逆順）
    rows_to_delete = []
    for i, r in enumerate(records):
        if str(r.get("section", "")) == section:
            rows_to_delete.append(i + 2)
    for row in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(row)

    # 新規追加
    if dates:
        next_id = _next_id(ws)
        actual_headers = _retry(ws.row_values, 1)
        rows = []
        for j, d in enumerate(sorted(dates)):
            values = {
                "id": next_id + j,
                "section": section,
                "date": d,
                "is_active": 1 if d in active_set else 0,
                "created_at": now,
            }
            rows.append([values.get(h, "") for h in actual_headers])
        if rows:
            _retry(ws.append_rows, rows)
    _clear_data_cache()


def toggle_target_date(section: str, date_str: str, is_active: bool):
    """特定の対象日の有効/無効を切り替え"""
    ws = _get_sheet("スケジュール対象日")
    records = _get_all_records(ws)
    actual_headers = _retry(ws.row_values, 1)
    for i, r in enumerate(records):
        if str(r.get("section", "")) == section and str(r.get("date", "")) == date_str:
            col = actual_headers.index("is_active") + 1
            ws.update_cell(i + 2, col, 1 if is_active else 0)
            _clear_data_cache()
            return
    # 存在しない場合は追加
    new_id = _next_id(ws)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    values = {
        "id": new_id,
        "section": section,
        "date": date_str,
        "is_active": 1 if is_active else 0,
        "created_at": now,
    }
    row = [values.get(h, "") for h in actual_headers]
    ws.append_row(row)
    _clear_data_cache()


# ---- 平日希望 CRUD ----

_weekday_pref_headers = [
    "doctor_id", "doctor_name", "ng_dates", "avoid_dates", "free_text", "updated_at",
]


def _get_weekday_pref_sheet(section: str):
    """セクション別の平日希望シートを取得/作成"""
    name = f"平日希望_{section}"
    return _init_monthly_sheet(name, _weekday_pref_headers)


@_register_cached
@st.cache_data(ttl=120)
def get_weekday_preferences(section: str):
    """指定セクションの全医員の希望を取得"""
    ws = _get_weekday_pref_sheet(section)
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["doctor_id"] = _safe_int(r["doctor_id"])
        r["ng_dates"] = _safe_json_loads(r.get("ng_dates"))
        r["avoid_dates"] = _safe_json_loads(r.get("avoid_dates"))
        r["free_text"] = str(r.get("free_text", "") or "")
        result.append(r)
    return result


def get_weekday_preference(doctor_id: int, section: str):
    """特定医員の希望を取得"""
    prefs = get_weekday_preferences(section)
    for p in prefs:
        if p["doctor_id"] == doctor_id:
            return p
    return None


def upsert_weekday_preference(doctor_id: int, section: str,
                               ng_dates=None, avoid_dates=None, free_text=None):
    """平日希望を登録/更新"""
    ws = _get_weekday_pref_sheet(section)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doctors = get_doctors(active_only=False)
    doc_name = next((d["name"] for d in doctors if d["id"] == doctor_id), "")

    data_map = {
        "doctor_id": str(doctor_id),
        "doctor_name": doc_name,
        "ng_dates": json.dumps(ng_dates or []),
        "avoid_dates": json.dumps(avoid_dates or []),
        "free_text": _sanitize_cell_value(free_text or ""),
        "updated_at": now,
    }
    row_data = [data_map.get(h, "") for h in _weekday_pref_headers]

    row_idx = _find_row_index(ws, 1, doctor_id)
    if row_idx:
        ws.update([row_data], f"A{row_idx}")
    else:
        ws.append_row(row_data)
    _clear_data_cache()


# ---- 平日スケジュール CRUD ----

_weekday_sched_headers = [
    "id", "section", "date", "slot_id", "slot_name",
    "doctor_id", "doctor_name", "created_at", "updated_at",
]


def _get_weekday_sched_sheet(year_month: str):
    """月別平日スケジュールシートを取得/作成"""
    name = f"平日スケジュール_{year_month}"
    return _init_monthly_sheet(name, _weekday_sched_headers)


@_register_cached
@st.cache_data(ttl=120)
def get_weekday_schedule(year_month: str, section: str = None):
    """平日スケジュールを取得"""
    ws = _get_weekday_sched_sheet(year_month)
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = _safe_int(r["id"])
        r["section"] = str(r.get("section", ""))
        r["date"] = str(r.get("date", ""))
        r["slot_id"] = _safe_int(r.get("slot_id", 0))
        r["slot_name"] = str(r.get("slot_name", ""))
        r["doctor_id"] = _safe_int(r.get("doctor_id", 0))
        r["doctor_name"] = str(r.get("doctor_name", ""))
        if section and r["section"] != section:
            continue
        result.append(r)
    result.sort(key=lambda x: (x["date"], x["slot_id"]))
    return result


def batch_save_weekday_assignments(year_month: str, section: str, assignments: dict):
    """平日スケジュールを一括保存

    Args:
        year_month: "YYYY-MM"
        section: セクションキー
        assignments: {date_str: {slot_id: [doctor_id, ...]}}
    """
    ws = _get_weekday_sched_sheet(year_month)
    records = _get_all_records(ws)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doctors = get_doctors(active_only=False)
    name_map = {d["id"]: d["name"] for d in doctors}
    slots = get_weekday_slots(section)
    slot_name_map = {s["id"]: s["slot_name"] for s in slots}

    # 当該セクションの既存行を削除（逆順）
    rows_to_delete = []
    for i, r in enumerate(records):
        if str(r.get("section", "")) == section:
            rows_to_delete.append(i + 2)
    for row in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(row)

    # 新規行を一括追加
    next_id = _next_id(ws)
    actual_headers = _retry(ws.row_values, 1)
    rows = []
    idx = 0
    for date_str in sorted(assignments.keys()):
        for slot_id, doc_ids in assignments[date_str].items():
            slot_id_int = int(slot_id) if isinstance(slot_id, str) else slot_id
            for doc_id in doc_ids:
                values = {
                    "id": next_id + idx,
                    "section": section,
                    "date": date_str,
                    "slot_id": slot_id_int,
                    "slot_name": slot_name_map.get(slot_id_int, ""),
                    "doctor_id": doc_id,
                    "doctor_name": name_map.get(doc_id, ""),
                    "created_at": now,
                    "updated_at": now,
                }
                rows.append([values.get(h, "") for h in actual_headers])
                idx += 1
    if rows:
        _retry(ws.append_rows, rows)
    _clear_data_cache()


def delete_weekday_assignment(year_month: str, assignment_id: int):
    """平日スケジュールの1行を削除"""
    ws = _get_weekday_sched_sheet(year_month)
    row_idx = _find_row_index(ws, 1, assignment_id)
    if row_idx:
        ws.delete_rows(row_idx)
    _clear_data_cache()


# ---- シフト交換 ----

_swap_headers = [
    "id", "section", "requester_id", "requester_name",
    "original_date", "original_slot_id",
    "target_id", "target_name", "target_date", "target_slot_id",
    "executed_at",
]


def _get_swap_sheet(year_month: str):
    """月別シフト交換シートを取得/作成"""
    name = f"シフト交換_{year_month}"
    return _init_monthly_sheet(name, _swap_headers)


def execute_swap(year_month: str, section: str,
                 requester_id: int, original_date: str, original_slot_id: int,
                 target_id: int, target_date: str, target_slot_id: int):
    """シフト交換を即時実行

    1. 平日スケジュールシートで2つの割り当てを入れ替え
    2. シフト交換シートにログを記録
    """
    ws_sched = _get_weekday_sched_sheet(year_month)
    records = _get_all_records(ws_sched)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doctors = get_doctors(active_only=False)
    name_map = {d["id"]: d["name"] for d in doctors}

    actual_headers = _retry(ws_sched.row_values, 1)
    doc_id_col = actual_headers.index("doctor_id") + 1
    doc_name_col = actual_headers.index("doctor_name") + 1
    updated_col = actual_headers.index("updated_at") + 1

    updates = []
    # requester の original → target に変更
    for i, r in enumerate(records):
        if (str(r.get("section", "")) == section
                and str(r.get("date", "")) == original_date
                and _safe_int(r.get("slot_id")) == original_slot_id
                and _safe_int(r.get("doctor_id")) == requester_id):
            row_num = i + 2
            updates.append({"range": f"{_col_letter(doc_id_col)}{row_num}", "values": [[target_id]]})
            updates.append({"range": f"{_col_letter(doc_name_col)}{row_num}", "values": [[name_map.get(target_id, "")]]})
            updates.append({"range": f"{_col_letter(updated_col)}{row_num}", "values": [[now]]})
            break

    # target の target_date → requester に変更
    for i, r in enumerate(records):
        if (str(r.get("section", "")) == section
                and str(r.get("date", "")) == target_date
                and _safe_int(r.get("slot_id")) == target_slot_id
                and _safe_int(r.get("doctor_id")) == target_id):
            row_num = i + 2
            updates.append({"range": f"{_col_letter(doc_id_col)}{row_num}", "values": [[requester_id]]})
            updates.append({"range": f"{_col_letter(doc_name_col)}{row_num}", "values": [[name_map.get(requester_id, "")]]})
            updates.append({"range": f"{_col_letter(updated_col)}{row_num}", "values": [[now]]})
            break

    if updates:
        _retry(ws_sched.batch_update, updates)

    # シフト交換ログに記録
    ws_swap = _get_swap_sheet(year_month)
    swap_id = _next_id(ws_swap)
    swap_headers = _retry(ws_swap.row_values, 1)
    swap_values = {
        "id": swap_id,
        "section": section,
        "requester_id": requester_id,
        "requester_name": name_map.get(requester_id, ""),
        "original_date": original_date,
        "original_slot_id": original_slot_id,
        "target_id": target_id,
        "target_name": name_map.get(target_id, ""),
        "target_date": target_date,
        "target_slot_id": target_slot_id,
        "executed_at": now,
    }
    swap_row = [swap_values.get(h, "") for h in swap_headers]
    ws_swap.append_row(swap_row)
    _clear_data_cache()


@_register_cached
@st.cache_data(ttl=120)
def get_swap_history(year_month: str, section: str = None):
    """シフト交換履歴を取得"""
    ws = _get_swap_sheet(year_month)
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = _safe_int(r["id"])
        r["section"] = str(r.get("section", ""))
        r["requester_id"] = _safe_int(r.get("requester_id", 0))
        r["target_id"] = _safe_int(r.get("target_id", 0))
        if section and r["section"] != section:
            continue
        result.append(r)
    return result
