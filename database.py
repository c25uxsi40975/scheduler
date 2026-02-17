"""
データベース管理モジュール
Google スプレッドシートで医員・外勤先・希望・スケジュールを永続化
マスタ用と運用データ用の2スプレッドシート対応
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
def _get_master_spreadsheet():
    """マスタ用スプレッドシートに接続（認証キャッシュ付き）"""
    credentials = st.secrets["gcp_service_account"]
    gc = gspread.service_account_from_dict(dict(credentials))
    spreadsheet_key = st.secrets.get("spreadsheet_key", "")
    if spreadsheet_key:
        return gc.open_by_key(spreadsheet_key)
    return gc.open(st.secrets.get("spreadsheet_name", "外勤調整データ"))


@st.cache_resource
def _get_operational_spreadsheet():
    """運用データ用スプレッドシートに接続（未設定時はマスタにフォールバック）"""
    op_key = st.secrets.get("spreadsheet_key_operational", "")
    if op_key:
        credentials = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(dict(credentials))
        return gc.open_by_key(op_key)
    return _get_master_spreadsheet()


def _get_spreadsheet_for(sheet_name: str):
    """シート名から適切なスプレッドシートを返す"""
    if _is_operational_sheet(sheet_name):
        return _get_operational_spreadsheet()
    return _get_master_spreadsheet()


def _retry(func, *args, max_retries=3, **kwargs):
    """API呼び出しをリトライ付きで実行"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise


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
    master_existing = {ws.title: ws for ws in _retry(sh_master.worksheets)}
    for name, ws in master_existing.items():
        if _is_operational_sheet(name):
            _ws_cache_operational[name] = ws
        else:
            _ws_cache_master[name] = ws

    # 運用スプレッドシートが別の場合、そちらもキャッシュ構築
    sh_op = _get_operational_spreadsheet()
    if sh_op is not sh_master:
        op_existing = {ws.title: ws for ws in _retry(sh_op.worksheets)}
        _ws_cache_operational.update(op_existing)

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

    # 既存医員でパスワード未設定の場合、初期パスワード「1111」を設定
    ws = _get_sheet("医員マスタ")
    headers = ws.row_values(1)
    if "password_hash" in headers:
        col_idx = headers.index("password_hash") + 1
        records = ws.get_all_values()
        default_pw_hash = _hash_password("1111")
        for i, row in enumerate(records[1:], start=2):
            pw_val = row[col_idx - 1] if len(row) >= col_idx else ""
            if not pw_val:
                ws.update_cell(i, col_idx, default_pw_hash)


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


# ---- Doctor CRUD ----

def get_doctors(active_only=True):
    ws = _get_sheet("医員マスタ")
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = int(r["id"])
        r["email"] = str(r.get("email", ""))
        r["password_hash"] = str(r.get("password_hash", ""))
        r["is_active"] = int(r.get("is_active", 1))
        if active_only and not r["is_active"]:
            continue
        result.append(r)
    result.sort(key=lambda x: x["name"])
    return result


def add_doctor(name):
    ws = _get_sheet("医員マスタ")
    # 重複チェック
    records = _get_all_records(ws)
    if any(r["name"] == name for r in records):
        return
    new_id = _next_id(ws)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    default_pw_hash = _hash_password("1111")
    ws.append_row([new_id, name, "", default_pw_hash, 1, now])


def update_doctor(doc_id, name=None, is_active=None):
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doc_id)
    if not row_idx:
        return
    headers = ws.row_values(1)
    if name is not None:
        col_idx = headers.index("name") + 1
        ws.update_cell(row_idx, col_idx, name)
    if is_active is not None:
        col_idx = headers.index("is_active") + 1
        ws.update_cell(row_idx, col_idx, int(is_active))


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

    # 希望シートから削除（全月）（運用データ）
    sh_op = _get_operational_spreadsheet()
    for ws in sh_op.worksheets():
        if ws.title.startswith("希望_"):
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


# ---- Clinic CRUD ----

def get_clinics(active_only=True):
    ws = _get_sheet("外勤先マスタ")
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["id"] = int(r["id"])
        r["fee"] = int(r.get("fee", 0))
        r["is_active"] = int(r.get("is_active", 1))
        r["preferred_doctors"] = _safe_json_loads(r.get("preferred_doctors", "[]"))
        if active_only and not r["is_active"]:
            continue
        result.append(r)
    result.sort(key=lambda x: x["name"])
    return result


def add_clinic(name, fee=0, frequency="weekly", preferred_doctors=None):
    ws = _get_sheet("外勤先マスタ")
    records = _get_all_records(ws)
    if any(r["name"] == name for r in records):
        return
    new_id = _next_id(ws)
    pref = json.dumps(preferred_doctors or [])
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws.append_row([new_id, name, fee, frequency, pref, 1, now])


def update_clinic(clinic_id, **kwargs):
    ws = _get_sheet("外勤先マスタ")
    row_idx = _find_row_index(ws, 1, clinic_id)
    if not row_idx:
        return
    headers = ws.row_values(1)
    for key, val in kwargs.items():
        if key == "preferred_doctors":
            val = json.dumps(val)
        if key in headers:
            col_idx = headers.index(key) + 1
            ws.update_cell(row_idx, col_idx, val)


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
    records = _get_all_records(ws_ovr)
    rows_to_delete = []
    for i, r in enumerate(records):
        if str(r.get("clinic_id", "")) == str(clinic_id):
            rows_to_delete.append(i + 2)
    for row in sorted(rows_to_delete, reverse=True):
        ws_ovr.delete_rows(row)

    # 外勤先マスタから削除
    ws_cli = _get_sheet("外勤先マスタ")
    row_idx = _find_row_index(ws_cli, 1, clinic_id)
    if row_idx:
        ws_cli.delete_rows(row_idx)


# ---- Preferences ----

def _get_pref_sheet(year_month):
    """月別希望シートを取得/作成"""
    name = f"希望_{year_month}"
    headers = ["doctor_id", "doctor_name", "ng_dates", "avoid_dates", "preferred_clinics", "updated_at"]
    return _init_monthly_sheet(name, headers)


def get_preference(doctor_id, year_month):
    ws = _get_pref_sheet(year_month)
    records = _get_all_records(ws)
    for r in records:
        if str(r.get("doctor_id", "")) == str(doctor_id):
            r["doctor_id"] = int(r["doctor_id"])
            r["ng_dates"] = _safe_json_loads(r.get("ng_dates"))
            r["avoid_dates"] = _safe_json_loads(r.get("avoid_dates"))
            r["preferred_clinics"] = _safe_json_loads(r.get("preferred_clinics"))
            return r
    return None


def get_all_preferences(year_month):
    ws = _get_pref_sheet(year_month)
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["doctor_id"] = int(r["doctor_id"])
        r["ng_dates"] = _safe_json_loads(r.get("ng_dates"))
        r["avoid_dates"] = _safe_json_loads(r.get("avoid_dates"))
        r["preferred_clinics"] = _safe_json_loads(r.get("preferred_clinics"))
        result.append(r)
    return result


def upsert_preference(doctor_id, year_month, ng_dates=None, avoid_dates=None, preferred_clinics=None):
    ws = _get_pref_sheet(year_month)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ng = json.dumps(ng_dates or [])
    av = json.dumps(avoid_dates or [])
    pc = json.dumps(preferred_clinics or [])

    # 医員名を取得
    doctors = get_doctors(active_only=False)
    doc_name = ""
    for d in doctors:
        if d["id"] == doctor_id:
            doc_name = d["name"]
            break

    # 既存行を探す
    row_idx = _find_row_index(ws, 1, doctor_id)
    if row_idx:
        ws.update([[str(doctor_id), doc_name, ng, av, pc, now]], f"A{row_idx}")
    else:
        ws.append_row([str(doctor_id), doc_name, ng, av, pc, now])


# ---- Affinity ----

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
            return
    ws.append_row([str(doctor_id), str(clinic_id), weight])


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
            return

    new_id = _next_id(ws)
    ws.append_row([new_id, plan_name, json.dumps(assignments), total_variance, satisfaction_score, 0, now])


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
    """スケジュールを確定（運用データスプレッドシートを走査）"""
    sh = _get_operational_spreadsheet()
    for ws in sh.worksheets():
        if not ws.title.startswith("スケジュール_"):
            continue
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                # 同月の全プランを未確定にリセット
                for j in range(len(records)):
                    ws.update_cell(j + 2, 6, 0)
                # 対象プランを確定
                ws.update_cell(i + 2, 6, 1)
                return


def delete_schedule(schedule_id):
    sh = _get_operational_spreadsheet()
    for ws in sh.worksheets():
        if not ws.title.startswith("スケジュール_"):
            continue
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                ws.delete_rows(i + 2)
                return


def update_schedule_assignments(schedule_id, assignments):
    sh = _get_operational_spreadsheet()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for ws in sh.worksheets():
        if not ws.title.startswith("スケジュール_"):
            continue
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                ws.update_cell(i + 2, 3, json.dumps(assignments))
                ws.update_cell(i + 2, 7, now)
                return


def get_all_confirmed_schedules():
    """全月の確定スケジュールを取得（累計報酬計算用）"""
    sh = _get_operational_spreadsheet()
    result = []
    for ws in sh.worksheets():
        if not ws.title.startswith("スケジュール_"):
            continue
        year_month = ws.title.replace("スケジュール_", "")
        records = _get_all_records(ws)
        for r in records:
            if int(r.get("is_confirmed", 0)):
                r["id"] = int(r["id"])
                r["year_month"] = year_month
                r["assignments"] = _safe_json_loads(r.get("assignments"))
                result.append(r)
    result.sort(key=lambda x: x.get("year_month", ""))
    return result


def get_confirmed_months():
    """確定済みスケジュールが存在する月のリストを返す"""
    sh = _get_operational_spreadsheet()
    months = []
    for ws in sh.worksheets():
        if not ws.title.startswith("スケジュール_"):
            continue
        year_month = ws.title.replace("スケジュール_", "")
        records = _get_all_records(ws)
        for r in records:
            if int(r.get("is_confirmed", 0)):
                months.append(year_month)
                break
    months.sort(reverse=True)
    return months


# ---- Settings / Auth ----

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _get_setting(key):
    ws = _get_sheet("設定")
    records = _get_all_records(ws)
    for r in records:
        if r.get("key") == key:
            return r.get("value")
    return None


def _set_setting(key, value):
    ws = _get_sheet("設定")
    row_idx = _find_row_index(ws, 1, key)
    if row_idx:
        ws.update_cell(row_idx, 2, value)
    else:
        ws.append_row([key, value])


def is_admin_password_set() -> bool:
    return _get_setting("admin_password") is not None


def set_admin_password(password: str):
    _set_setting("admin_password", _hash_password(password))


def verify_admin_password(password: str) -> bool:
    stored = _get_setting("admin_password")
    if not stored:
        return False
    return stored == _hash_password(password)


def is_doctor_individual_password_set(doctor_id) -> bool:
    """医員の個別パスワードが設定済みか"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return False
    headers = ws.row_values(1)
    if "password_hash" not in headers:
        return False
    col_idx = headers.index("password_hash") + 1
    val = ws.cell(row_idx, col_idx).value
    return bool(val)


def set_doctor_individual_password(doctor_id, password: str):
    """医員の個別パスワードを設定"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return
    headers = ws.row_values(1)
    col_idx = headers.index("password_hash") + 1
    ws.update_cell(row_idx, col_idx, _hash_password(password))


def verify_doctor_individual_password(doctor_id, password: str) -> bool:
    """医員の個別パスワードを検証"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return False
    headers = ws.row_values(1)
    if "password_hash" not in headers:
        return False
    col_idx = headers.index("password_hash") + 1
    stored = ws.cell(row_idx, col_idx).value
    if not stored:
        return False
    return stored == _hash_password(password)


def update_doctor_email(doctor_id, email: str):
    """医員のメールアドレスを設定/更新"""
    ws = _get_sheet("医員マスタ")
    row_idx = _find_row_index(ws, 1, doctor_id)
    if not row_idx:
        return
    headers = ws.row_values(1)
    col_idx = headers.index("email") + 1
    ws.update_cell(row_idx, col_idx, email)


# ---- Open Month (対象月制御) ----

def get_open_month():
    """医員が希望入力可能な月を取得"""
    return _get_setting("open_month")


def set_open_month(year_month: str):
    """医員が希望入力可能な月を設定"""
    _set_setting("open_month", year_month)


# ---- Clinic Date Overrides ----

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
    ws = _get_sheet("日別設定")
    records = _get_all_records(ws)

    # 既存行を探す
    for i, r in enumerate(records):
        if str(r.get("clinic_id", "")) == str(clinic_id) and str(r.get("date", "")) == date_str:
            if required_doctors == 1:
                ws.delete_rows(i + 2)
            else:
                ws.update([[str(clinic_id), date_str, required_doctors]], f"A{i+2}")
            return

    # 新規（通常=1以外のみ保存）
    if required_doctors != 1:
        ws.append_row([str(clinic_id), date_str, required_doctors])


def delete_old_schedules(months_to_keep=4):
    """古い月別シートを削除（運用データスプレッドシート）"""
    from dateutil.relativedelta import relativedelta
    cutoff = (datetime.now() - relativedelta(months=months_to_keep)).strftime("%Y-%m")
    sh_op = _get_operational_spreadsheet()
    all_sheets = sh_op.worksheets()
    for ws in all_sheets:
        for prefix in ("希望_", "スケジュール_"):
            if ws.title.startswith(prefix):
                ym = ws.title.replace(prefix, "")
                if ym < cutoff:
                    sh_op.del_worksheet(ws)
                    _ws_cache_operational.pop(ws.title, None)
