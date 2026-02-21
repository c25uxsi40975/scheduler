"""
マスタスプレッドシートのCRUD操作
医員・外勤先・優先度・日別設定
"""
import json
from datetime import datetime
import streamlit as st

from database.connection import (
    _get_sheet, _get_all_records, _find_row_index, _next_id,
    _col_letter, _retry, _clear_data_cache, _register_cached,
    _safe_json_loads, _hash_password,
    _ws_cache_operational, SHEET_HEADERS,
)


# ---- Doctor CRUD ----

@_register_cached
@st.cache_data(ttl=120)
def get_doctors(active_only=True):
    ws = _get_sheet("医員マスタ")
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = int(r["id"])
        r["account"] = str(r.get("account", ""))
        r["account_name"] = str(r.get("account_name", "") or r.get("account", ""))
        r["email"] = str(r.get("email", ""))
        r["password_hash"] = str(r.get("password_hash", ""))
        r["is_active"] = int(r.get("is_active", 1))
        r["max_assignments"] = int(r.get("max_assignments", 0) or 0)
        if active_only and not r["is_active"]:
            continue
        result.append(r)
    result.sort(key=lambda x: x["name"])
    return result


def add_doctor(name, account="", initial_password="1111"):
    ws = _get_sheet("医員マスタ")
    # 重複チェック（ID）
    records = _get_all_records(ws)
    if account and any(str(r.get("account", "")) == account for r in records):
        return "duplicate_account"
    if any(r["name"] == name for r in records):
        return "duplicate_name"
    new_id = _next_id(ws)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pw_hash = _hash_password(initial_password)
    # 実際のヘッダー順序に基づいて行を構築（カラム追加時のずれ防止）
    actual_headers = _retry(ws.row_values, 1)
    values = {
        "id": new_id, "name": name, "account": account,
        "account_name": account, "email": "",
        "password_hash": pw_hash, "is_active": 1,
        "created_at": now, "max_assignments": 0,
    }
    row = [values.get(h, "") for h in actual_headers]
    ws.append_row(row)
    _clear_data_cache()
    return None


def update_doctor(doc_id, is_active=None, max_assignments=None):
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doc_id)
    if not row_idx:
        return
    actual_headers = _retry(ws.row_values, 1)
    updates = []
    if is_active is not None:
        col = actual_headers.index("is_active") + 1
        updates.append({'range': f'{_col_letter(col)}{row_idx}', 'values': [[int(is_active)]]})
    if max_assignments is not None:
        col = actual_headers.index("max_assignments") + 1
        updates.append({'range': f'{_col_letter(col)}{row_idx}', 'values': [[int(max_assignments)]]})
    if updates:
        _retry(ws.batch_update, updates)
    _clear_data_cache()


def delete_doctor(doc_id):
    # 優先度マスタから削除（マスタ）
    ws_aff = _get_sheet("優先度マスタ")
    records = _get_all_records(ws_aff)
    rows_to_delete = []
    for i, r in enumerate(records):
        if str(r.get("doctor_id", "")) == str(doc_id):
            rows_to_delete.append(i + 2)  # +2: ヘッダー + 0-index
    for row in sorted(rows_to_delete, reverse=True):
        ws_aff.delete_rows(row)

    # 希望シートから削除（キャッシュ済みシートを使用 — worksheets() API不要）
    for ws_name, ws in list(_ws_cache_operational.items()):
        if ws_name.startswith("希望_"):
            recs = _get_all_records(ws)
            for i, r in enumerate(recs):
                if str(r.get("doctor_id", "")) == str(doc_id):
                    ws.delete_rows(i + 2)
                    break

    # 医員マスタから削除（マスタ）
    ws_doc = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws_doc, 1, doc_id)
    if row_idx:
        ws_doc.delete_rows(row_idx)
    _clear_data_cache()


# ---- Clinic CRUD ----

@_register_cached
@st.cache_data(ttl=120)
def get_clinics(active_only=True):
    ws = _get_sheet("外勤先マスタ")
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = int(r["id"])
        r["fee"] = int(r.get("fee", 0))
        r["is_active"] = int(r.get("is_active", 1))
        r["preferred_doctors"] = _safe_json_loads(r.get("preferred_doctors", "[]"))
        r["fixed_doctors"] = _safe_json_loads(r.get("fixed_doctors", "[]"))
        if active_only and not r["is_active"]:
            continue
        result.append(r)
    result.sort(key=lambda x: x["name"])
    return result


def add_clinic(name, fee=0, frequency="weekly", preferred_doctors=None, fixed_doctors=None):
    ws = _get_sheet("外勤先マスタ")
    records = _get_all_records(ws)
    if any(r["name"] == name for r in records):
        return
    new_id = _next_id(ws)
    pref = json.dumps(preferred_doctors or [])
    fixed = json.dumps(fixed_doctors or [])
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    actual_headers = _retry(ws.row_values, 1)
    values = {
        "id": new_id, "name": name, "fee": fee, "frequency": frequency,
        "preferred_doctors": pref, "fixed_doctors": fixed,
        "is_active": 1, "created_at": now,
    }
    row = [values.get(h, "") for h in actual_headers]
    ws.append_row(row)
    _clear_data_cache()


def update_clinic(clinic_id, **kwargs):
    ws = _get_sheet("外勤先マスタ")
    row_idx = _find_row_index(ws, 1, clinic_id)
    if not row_idx:
        return
    headers = SHEET_HEADERS["外勤先マスタ"]
    updates = []
    for key, val in kwargs.items():
        if key in ("preferred_doctors", "fixed_doctors"):
            val = json.dumps(val)
        if key in headers:
            col = headers.index(key) + 1
            updates.append({'range': f'{_col_letter(col)}{row_idx}', 'values': [[val]]})
    if updates:
        _retry(ws.batch_update, updates)
    _clear_data_cache()


def delete_clinic(clinic_id):
    # 優先度マスタから削除
    ws_aff = _get_sheet("優先度マスタ")
    records = _get_all_records(ws_aff)
    rows_to_delete = []
    for i, r in enumerate(records):
        if str(r.get("clinic_id", "")) == str(clinic_id):
            rows_to_delete.append(i + 2)
    for row in sorted(rows_to_delete, reverse=True):
        ws_aff.delete_rows(row)

    # 日別設定から削除
    ws_ovr = _get_sheet("日別設定")
    records_ovr = _get_all_records(ws_ovr)
    rows_to_delete = []
    for i, r in enumerate(records_ovr):
        if str(r.get("clinic_id", "")) == str(clinic_id):
            rows_to_delete.append(i + 2)
    for row in sorted(rows_to_delete, reverse=True):
        ws_ovr.delete_rows(row)

    # 外勤先マスタから削除
    ws_cli = _get_sheet("外勤先マスタ")
    row_idx = _find_row_index(ws_cli, 1, clinic_id)
    if row_idx:
        ws_cli.delete_rows(row_idx)
    _clear_data_cache()


# ---- Affinity ----

@_register_cached
@st.cache_data(ttl=120)
def get_affinities():
    ws = _get_sheet("優先度マスタ")
    records = _get_all_records(ws)
    doctors = {d["id"]: d["name"] for d in get_doctors(active_only=False)}
    clinics = {c["id"]: c["name"] for c in get_clinics(active_only=False)}
    result = []
    for r in records:
        r["doctor_id"] = int(r["doctor_id"])
        r["clinic_id"] = int(r["clinic_id"])
        r["weight"] = float(r.get("weight", 1.0))
        r["doctor_name"] = doctors.get(r["doctor_id"], "")
        r["clinic_name"] = clinics.get(r["clinic_id"], "")
        result.append(r)
    return result


def set_affinity(doctor_id, clinic_id, weight):
    ws = _get_sheet("優先度マスタ")
    records = _get_all_records(ws)
    for i, r in enumerate(records):
        if str(r.get("doctor_id", "")) == str(doctor_id) and str(r.get("clinic_id", "")) == str(clinic_id):
            ws.update([[str(doctor_id), str(clinic_id), weight]], f"A{i+2}")
            _clear_data_cache()
            return
    ws.append_row([str(doctor_id), str(clinic_id), weight])
    _clear_data_cache()


# ---- Clinic Date Overrides ----

@_register_cached
@st.cache_data(ttl=120)
def get_clinic_date_overrides(year_month):
    """指定月のオーバーライドを {(clinic_id, date_str): required_doctors} で返す"""
    ws = _get_sheet("日別設定")
    records = _get_all_records(ws)
    result = {}
    for r in records:
        d = str(r.get("date", ""))
        if d.startswith(year_month):
            result[(int(r["clinic_id"]), d)] = int(r["required_doctors"])
    return result


def set_clinic_date_override(clinic_id, date_str, required_doctors):
    """単一のオーバーライドを設定（後方互換性用。一括保存はbatch版を使用）"""
    ws = _get_sheet("日別設定")
    records = _get_all_records(ws)

    # 既存行を探す
    for i, r in enumerate(records):
        if str(r.get("clinic_id", "")) == str(clinic_id) and str(r.get("date", "")) == date_str:
            if required_doctors == 1:
                ws.delete_rows(i + 2)
            else:
                ws.update([[str(clinic_id), date_str, required_doctors]], f"A{i+2}")
            _clear_data_cache()
            return

    # 新規（通常=1以外のみ保存）
    if required_doctors != 1:
        ws.append_row([str(clinic_id), date_str, required_doctors])
    _clear_data_cache()


def set_clinic_date_overrides_batch(changes: dict):
    """日別設定を一括保存（ループ呼び出しを1回のバッチ操作に統合）

    changes: {(clinic_id, date_str): required_doctors, ...}
    """
    if not changes:
        return

    ws = _get_sheet("日別設定")
    records = _get_all_records(ws)

    # 既存レコードのインデックスを構築
    existing = {}
    for i, r in enumerate(records):
        key = (str(r.get("clinic_id", "")), str(r.get("date", "")))
        existing[key] = i

    rows_to_delete = []
    updates = []
    appends = []

    for (clinic_id, date_str), req in changes.items():
        key = (str(clinic_id), date_str)
        if key in existing:
            row_num = existing[key] + 2
            if req == 1:
                rows_to_delete.append(row_num)
            else:
                updates.append({'range': f'A{row_num}', 'values': [[str(clinic_id), date_str, req]]})
        else:
            if req != 1:
                appends.append([str(clinic_id), date_str, req])

    # バッチ更新（1回のAPI呼出）
    if updates:
        _retry(ws.batch_update, updates)

    # 行削除（逆順で実行）
    for row in sorted(rows_to_delete, reverse=True):
        ws.delete_rows(row)

    # 新規行を一括追加（1回のAPI呼出）
    if appends:
        _retry(ws.append_rows, appends)

    _clear_data_cache()
