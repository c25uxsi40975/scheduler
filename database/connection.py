"""
接続・キャッシュ・リトライ・共有ユーティリティ
全ドメインモジュールがこのモジュールをインポートする
"""
import json
import hashlib
import time
from datetime import datetime
import gspread
import streamlit as st


def _safe_json_loads(val, default=None):
    """gspreadが自動パースしたリスト/dictにも対応するjson.loads"""
    if default is None:
        default = []
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str) and val:
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return default
    return default


# ---- スプレッドシート接続（2系統） ----

_OPERATIONAL_PREFIXES = ("希望_", "スケジュール_")


def _is_operational_sheet(name: str) -> bool:
    """運用データ用スプレッドシートに属するシートか判定"""
    return any(name.startswith(p) for p in _OPERATIONAL_PREFIXES)


@st.cache_resource
def _get_gspread_client():
    """gspreadクライアントを取得（認証は1回のみ）"""
    credentials = st.secrets["gcp_service_account"]
    return gspread.service_account_from_dict(dict(credentials))


@st.cache_resource
def _get_master_spreadsheet():
    """マスタ用スプレッドシートに接続"""
    gc = _get_gspread_client()
    spreadsheet_key = st.secrets.get("spreadsheet_key", "")
    if spreadsheet_key:
        return gc.open_by_key(spreadsheet_key)
    return gc.open(st.secrets.get("spreadsheet_name", "外勤調整データ"))


@st.cache_resource
def _get_operational_spreadsheet():
    """運用データ用スプレッドシートに接続（必須）"""
    gc = _get_gspread_client()
    return gc.open_by_key(st.secrets["spreadsheet_key_operational"])


def _get_spreadsheet_for(sheet_name: str):
    """シート名から適切なスプレッドシートを返す"""
    if _is_operational_sheet(sheet_name):
        return _get_operational_spreadsheet()
    return _get_master_spreadsheet()


def _retry(func, *args, max_retries=5, **kwargs):
    """API呼び出しをリトライ付きで実行（レート制限対応）"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)  # 2, 4, 8, 16秒
                # 429 Rate Limitの場合はさらに長く待つ
                if hasattr(e, 'response') and getattr(e.response, 'status_code', 0) == 429:
                    wait = max(wait, 10)
                time.sleep(wait)
            else:
                raise


_CACHED_FUNCTIONS = []


def _register_cached(func):
    """@st.cache_data で装飾された関数を登録（一括クリア用）"""
    _CACHED_FUNCTIONS.append(func)
    return func


def _clear_data_cache():
    """データベース読み取りキャッシュをクリア"""
    for func in _CACHED_FUNCTIONS:
        func.clear()


def _col_letter(col_idx):
    """1-indexed列番号をアルファベットに変換 (1='A', 2='B', ...)"""
    return chr(64 + col_idx)


_ws_cache_master = {}
_ws_cache_operational = {}


def _get_ws_cache(sheet_name: str) -> dict:
    """シート名に対応するキャッシュを返す"""
    if _is_operational_sheet(sheet_name):
        return _ws_cache_operational
    return _ws_cache_master


def _get_sheet(name):
    """シートを取得（キャッシュ+リトライ付き）。なければ新規作成"""
    cache = _get_ws_cache(name)
    if name in cache:
        return cache[name]
    sh = _get_spreadsheet_for(name)
    try:
        ws = _retry(sh.worksheet, name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=100, cols=20)
    cache[name] = ws
    return ws


def _get_all_records(ws):
    """シートの全レコードを辞書リストで取得（リトライ付き）"""
    return _retry(ws.get_all_records)


def _find_row_index(ws, col, value):
    """指定列でvalueが一致する行番号を返す（1-indexed、ヘッダー=1行目）"""
    col_values = _retry(ws.col_values, col)
    for i, v in enumerate(col_values):
        if i == 0:
            continue  # ヘッダー行スキップ
        if str(v) == str(value):
            return i + 1  # gspreadは1-indexed
    return None


def _next_id(ws):
    """idカラム(A列)の最大値+1を返す"""
    col_values = _retry(ws.col_values, 1)
    if len(col_values) <= 1:
        return 1
    ids = [int(v) for v in col_values[1:] if v.isdigit()]
    return max(ids) + 1 if ids else 1


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ---- 初期化 ----

SHEET_HEADERS = {
    "医員マスタ": ["id", "name", "email", "password_hash", "is_active", "created_at"],
    "外勤先マスタ": ["id", "name", "fee", "frequency", "preferred_doctors", "is_active", "created_at"],
    "優先度マスタ": ["doctor_id", "clinic_id", "weight"],
    "日別設定": ["clinic_id", "date", "required_doctors"],
    "設定": ["key", "value"],
}


_db_initialized = False


def init_db():
    """全シートを初期化（ヘッダーがなければ作成、不足カラムがあれば追加）"""
    global _db_initialized
    if _db_initialized:
        return

    # マスタスプレッドシートのキャッシュ構築
    sh_master = _get_master_spreadsheet()
    for ws in _retry(sh_master.worksheets):
        _ws_cache_master[ws.title] = ws

    # 運用スプレッドシートのキャッシュ構築（必ず別スプレッドシート）
    sh_op = _get_operational_spreadsheet()
    for ws in _retry(sh_op.worksheets):
        _ws_cache_operational[ws.title] = ws

    # マスタシートのヘッダー初期化
    for sheet_name, headers in SHEET_HEADERS.items():
        if sheet_name not in _ws_cache_master:
            ws = sh_master.add_worksheet(title=sheet_name, rows=100, cols=len(headers))
            ws.update([headers], "A1")
            _ws_cache_master[sheet_name] = ws
        else:
            ws = _ws_cache_master[sheet_name]
            existing_headers = _retry(ws.row_values, 1)
            if not existing_headers:
                ws.update([headers], "A1")
            else:
                # 不足カラムを末尾に追加（既存データとの互換性）
                missing = [h for h in headers if h not in existing_headers]
                if missing:
                    new_headers = existing_headers + missing
                    ws.update([new_headers], "A1")
    _db_initialized = True

    # 既存医員でパスワード未設定の場合、初期パスワード「1111」を設定（バッチ更新）
    ws = _get_sheet("医員マスタ")
    headers = ws.row_values(1)
    if "password_hash" in headers:
        col_idx = headers.index("password_hash") + 1
        records = ws.get_all_values()
        default_pw_hash = _hash_password("1111")
        updates = []
        col_l = _col_letter(col_idx)
        for i, row in enumerate(records[1:], start=2):
            pw_val = row[col_idx - 1] if len(row) >= col_idx else ""
            if not pw_val:
                updates.append({'range': f'{col_l}{i}', 'values': [[default_pw_hash]]})
        if updates:
            _retry(ws.batch_update, updates)


def _init_monthly_sheet(name, headers):
    """月別シートを初期化（キャッシュ+リトライ付き）"""
    cache = _get_ws_cache(name)
    if name in cache:
        return cache[name]
    sh = _get_spreadsheet_for(name)
    try:
        ws = _retry(sh.worksheet, name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=100, cols=len(headers))
    if not _retry(ws.row_values, 1):
        ws.update([headers], "A1")
    cache[name] = ws
    return ws
