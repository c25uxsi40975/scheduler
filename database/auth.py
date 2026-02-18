"""
認証・設定・パスワード・メール・対象月制御
"""
import streamlit as st

from database.connection import (
    _get_sheet, _get_all_records, _clear_data_cache,
    _register_cached, _find_row_index, _hash_password,
    SHEET_HEADERS,
)
from database.master import get_doctors


# ---- Settings ----

@_register_cached
@st.cache_data(ttl=120)
def _get_all_settings():
    """設定シートの全レコードをキャッシュ付きで取得"""
    ws = _get_sheet("設定")
    return _get_all_records(ws)


def _get_setting(key):
    records = _get_all_settings()
    for r in records:
        if r.get("key") == key:
            return r.get("value")
    return None


def _set_setting(key, value):
    ws = _get_sheet("設定")
    records = _get_all_records(ws)
    for i, r in enumerate(records):
        if r.get("key") == key:
            ws.update_cell(i + 2, 2, value)
            _clear_data_cache()
            return
    ws.append_row([key, value])
    _clear_data_cache()


# ---- Admin Auth ----

def is_admin_password_set() -> bool:
    return _get_setting("admin_password") is not None


def set_admin_password(password: str):
    _set_setting("admin_password", _hash_password(password))


def verify_admin_password(password: str) -> bool:
    stored = _get_setting("admin_password")
    if not stored:
        return False
    return stored == _hash_password(password)


# ---- Doctor Auth ----

def is_doctor_individual_password_set(doctor_id) -> bool:
    """医員の個別パスワードが設定済みか（キャッシュ済みデータを使用）"""
    doctors = get_doctors(active_only=False)
    for d in doctors:
        if d["id"] == doctor_id:
            return bool(d.get("password_hash"))
    return False


def set_doctor_individual_password(doctor_id, password: str):
    """医員の個別パスワードを設定"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return
    headers = SHEET_HEADERS["医員マスタ"]
    col_idx = headers.index("password_hash") + 1
    ws.update_cell(row_idx, col_idx, _hash_password(password))
    _clear_data_cache()


def verify_doctor_individual_password(doctor_id, password: str) -> bool:
    """医員の個別パスワードを検証（キャッシュ済みデータを使用）"""
    doctors = get_doctors(active_only=False)
    for d in doctors:
        if d["id"] == doctor_id:
            stored = d.get("password_hash", "")
            if not stored:
                return False
            return stored == _hash_password(password)
    return False


# ---- Doctor Email ----

def update_doctor_email(doctor_id, email: str):
    """医員のメールアドレスを設定/更新"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return
    headers = SHEET_HEADERS["医員マスタ"]
    col_idx = headers.index("email") + 1
    ws.update_cell(row_idx, col_idx, email)
    _clear_data_cache()


# ---- Open Month (対象月制御) ----

@_register_cached
@st.cache_data(ttl=120)
def get_open_month():
    """医員が希望入力可能な月を取得"""
    return _get_setting("open_month")


def set_open_month(year_month: str):
    """医員が希望入力可能な月を設定"""
    _set_setting("open_month", year_month)
