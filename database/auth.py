"""
認証・設定・パスワード・メール・対象月制御・パスワードリセット
"""
import json
import time
import streamlit as st

from database.connection import (
    _get_sheet, _get_all_records, _clear_data_cache,
    _register_cached, _find_row_index, _hash_password,
    _verify_password, _is_legacy_hash,
    _retry,
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
    if not _verify_password(password, stored):
        return False
    # 透過的リハッシュ: レガシー SHA-256 → bcrypt に自動移行
    if _is_legacy_hash(stored):
        _set_setting("admin_password", _hash_password(password))
    return True


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
    actual_headers = _retry(ws.row_values, 1)
    col_idx = actual_headers.index("password_hash") + 1
    ws.update_cell(row_idx, col_idx, _hash_password(password))
    _clear_data_cache()


def clear_must_change_pw(doctor_id):
    """初回パスワード変更完了フラグをクリア"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return
    actual_headers = _retry(ws.row_values, 1)
    col_idx = actual_headers.index("must_change_pw") + 1
    ws.update_cell(row_idx, col_idx, 0)
    _clear_data_cache()


def verify_doctor_individual_password(doctor_id, password: str) -> bool:
    """医員の個別パスワードを検証（キャッシュ済みデータを使用）"""
    doctors = get_doctors(active_only=False)
    for d in doctors:
        if d["id"] == doctor_id:
            stored = d.get("password_hash", "")
            if not stored:
                return False
            if not _verify_password(password, stored):
                return False
            # 透過的リハッシュ: レガシー SHA-256 → bcrypt に自動移行
            if _is_legacy_hash(stored):
                set_doctor_individual_password(doctor_id, password)
            return True
    return False


def get_doctor_by_account(account_name: str):
    """アカウント名で医員を検索。ログイン可能な医員のみ。"""
    doctors = get_doctors(active_only=False)
    for d in doctors:
        if d.get("account_name") == account_name and d.get("can_login", 1):
            return d
    return None


def verify_doctor_by_account(account_name: str, password: str):
    """アカウント名とパスワードで認証。成功時は医員dictを返す。"""
    doctor = get_doctor_by_account(account_name)
    if not doctor:
        return None
    stored = doctor.get("password_hash", "")
    if not stored:
        return None
    if not _verify_password(password, stored):
        return None
    # 透過的リハッシュ: レガシー SHA-256 → bcrypt に自動移行
    if _is_legacy_hash(stored):
        set_doctor_individual_password(doctor["id"], password)
    return doctor


def update_doctor_account_name(doctor_id, account_name: str):
    """医員のアカウント名を更新（ユーザー自身で変更可能）"""
    # 重複チェック
    doctors = get_doctors(active_only=False)
    for d in doctors:
        if d["id"] != doctor_id and d.get("account_name") == account_name:
            return "duplicate"
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return
    actual_headers = _retry(ws.row_values, 1)
    col_idx = actual_headers.index("account_name") + 1
    ws.update_cell(row_idx, col_idx, account_name)
    _clear_data_cache()
    return None


# ---- Doctor Email ----

def update_doctor_email(doctor_id, email: str):
    """医員のメールアドレスを設定/更新"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return
    actual_headers = _retry(ws.row_values, 1)
    col_idx = actual_headers.index("email") + 1
    ws.update_cell(row_idx, col_idx, email)
    _clear_data_cache()


# ---- Password Reset (パスワードリセット) ----

_RESET_CODE_TTL = 900  # 15分


def save_reset_code(account_name: str, code: str):
    """リセットコードを設定シートに保存（有効期限付き）"""
    data = json.dumps({"code": code, "expires": time.time() + _RESET_CODE_TTL})
    _set_setting(f"reset_code_{account_name}", data)


def verify_reset_code(account_name: str, code: str) -> bool:
    """リセットコードを検証。成功したらコードを削除。"""
    raw = _get_setting(f"reset_code_{account_name}")
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if time.time() > data.get("expires", 0):
        # 期限切れ — コードを削除
        _set_setting(f"reset_code_{account_name}", "")
        return False
    if data.get("code") != code:
        return False
    # 成功 — コードを削除
    _set_setting(f"reset_code_{account_name}", "")
    return True


def get_doctor_email_by_account(account_name: str) -> str | None:
    """アカウント名から医員のメールアドレスを取得"""
    doctors = get_doctors(active_only=False)
    for d in doctors:
        if d.get("account_name") == account_name:
            return d.get("email", "") or None
    return None


def get_doctor_id_by_account(account_name: str) -> int | None:
    """アカウント名から医員IDを取得（can_loginに関わらず）"""
    doctors = get_doctors(active_only=False)
    for d in doctors:
        if d.get("account_name") == account_name:
            return d["id"]
    return None


# ---- Open Month (対象月制御) ----

@_register_cached
@st.cache_data(ttl=120)
def get_open_month():
    """医員が希望入力可能な月を取得"""
    return _get_setting("open_month")


def set_open_month(year_month: str):
    """医員が希望入力可能な月を設定"""
    _set_setting("open_month", year_month)


# ---- Input Deadline (入力期限) ----

@_register_cached
@st.cache_data(ttl=120)
def get_input_deadline():
    """希望入力の期限日を取得（YYYY-MM-DD形式 or None）"""
    return _get_setting("input_deadline")


def set_input_deadline(deadline_date: str):
    """希望入力の期限日を設定（YYYY-MM-DD形式）"""
    _set_setting("input_deadline", deadline_date)
