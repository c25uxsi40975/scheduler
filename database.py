"""
データベース管理モジュール
Google スプレッドシートで医員・外勤先・希望・スケジュールを永続化
"""
import json
import hashlib
from datetime import datetime
import gspread
import streamlit as st


# ---- スプレッドシート接続 ----

@st.cache_resource
def _get_spreadsheet():
    """Google スプレッドシートに接続（認証キャッシュ付き）"""
    credentials = st.secrets["gcp_service_account"]
    gc = gspread.service_account_from_dict(dict(credentials))
    # スプレッドシートキーで接続（名前検索より確実）
    spreadsheet_key = st.secrets.get("spreadsheet_key", "")
    if spreadsheet_key:
        return gc.open_by_key(spreadsheet_key)
    return gc.open(st.secrets.get("spreadsheet_name", "外勤調整データ"))


def _get_sheet(name):
    """シートを取得。なければヘッダー付きで新規作成"""
    sh = _get_spreadsheet()
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=100, cols=20)
        return ws


def _get_all_records(ws):
    """シートの全レコードを辞書リストで取得"""
    data = ws.get_all_records()
    return data


def _find_row_index(ws, col, value):
    """指定列でvalueが一致する行番号を返す（1-indexed、ヘッダー=1行目）"""
    col_values = ws.col_values(col)
    for i, v in enumerate(col_values):
        if i == 0:
            continue  # ヘッダー行スキップ
        if str(v) == str(value):
            return i + 1  # gspreadは1-indexed
    return None


def _next_id(ws):
    """idカラム(A列)の最大値+1を返す"""
    col_values = ws.col_values(1)
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


def init_db():
    """全シートを初期化（ヘッダーがなければ作成、不足カラムがあれば追加）"""
    sh = _get_spreadsheet()
    for sheet_name, headers in SHEET_HEADERS.items():
        try:
            ws = sh.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=len(headers))
        existing_headers = ws.row_values(1)
        if not existing_headers:
            ws.update([headers], "A1")
        else:
            # 不足カラムを末尾に追加（既存データとの互換性）
            missing = [h for h in headers if h not in existing_headers]
            if missing:
                new_headers = existing_headers + missing
                ws.update([new_headers], "A1")


def _init_monthly_sheet(name, headers):
    """月別シートを初期化"""
    sh = _get_spreadsheet()
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=100, cols=len(headers))
    if not ws.row_values(1):
        ws.update([headers], "A1")
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
    ws.append_row([new_id, name, "", "", 1, now])


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
    # 優先度マスタから削除
    ws_aff = _get_sheet("優先度マスタ")
    records = _get_all_records(ws_aff)
    rows_to_delete = []
    for i, r in enumerate(records):
        if str(r.get("doctor_id", "")) == str(doc_id):
            rows_to_delete.append(i + 2)  # +2: ヘッダー + 0-index
    for row in sorted(rows_to_delete, reverse=True):
        ws_aff.delete_rows(row)

    # 希望シートから削除（全月）
    sh = _get_spreadsheet()
    for ws in sh.worksheets():
        if ws.title.startswith("希望_"):
            recs = _get_all_records(ws)
            for i, r in enumerate(recs):
                if str(r.get("doctor_id", "")) == str(doc_id):
                    ws.delete_rows(i + 2)
                    break

    # 医員マスタから削除
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
        # preferred_doctorsをパース
        pd_raw = r.get("preferred_doctors", "[]")
        if isinstance(pd_raw, str) and pd_raw:
            try:
                r["preferred_doctors"] = json.loads(pd_raw)
            except (json.JSONDecodeError, ValueError):
                r["preferred_doctors"] = []
        else:
            r["preferred_doctors"] = []
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
            r["ng_dates"] = json.loads(r.get("ng_dates") or "[]")
            r["avoid_dates"] = json.loads(r.get("avoid_dates") or "[]")
            r["preferred_clinics"] = json.loads(r.get("preferred_clinics") or "[]")
            return r
    return None


def get_all_preferences(year_month):
    ws = _get_pref_sheet(year_month)
    records = _get_all_records(ws)
    result = []
    for r in records:
        r["doctor_id"] = int(r["doctor_id"])
        r["ng_dates"] = json.loads(r.get("ng_dates") or "[]")
        r["avoid_dates"] = json.loads(r.get("avoid_dates") or "[]")
        r["preferred_clinics"] = json.loads(r.get("preferred_clinics") or "[]")
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
        try:
            r["assignments"] = json.loads(r.get("assignments", "[]"))
        except (json.JSONDecodeError, TypeError):
            r["assignments"] = []
        result.append(r)
    return result


def confirm_schedule(schedule_id):
    # 現在のシートを特定する必要がある → 全スケジュールシートを走査
    sh = _get_spreadsheet()
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
    sh = _get_spreadsheet()
    for ws in sh.worksheets():
        if not ws.title.startswith("スケジュール_"):
            continue
        records = _get_all_records(ws)
        for i, r in enumerate(records):
            if str(r.get("id", "")) == str(schedule_id):
                ws.delete_rows(i + 2)
                return


def update_schedule_assignments(schedule_id, assignments):
    sh = _get_spreadsheet()
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
    sh = _get_spreadsheet()
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
                try:
                    r["assignments"] = json.loads(r.get("assignments", "[]"))
                except (json.JSONDecodeError, TypeError):
                    r["assignments"] = []
                result.append(r)
    result.sort(key=lambda x: x.get("year_month", ""))
    return result


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
    """古い月別シートを削除"""
    from dateutil.relativedelta import relativedelta
    cutoff = (datetime.now() - relativedelta(months=months_to_keep)).strftime("%Y-%m")
    sh = _get_spreadsheet()
    for ws in sh.worksheets():
        for prefix in ("希望_", "スケジュール_"):
            if ws.title.startswith(prefix):
                ym = ws.title.replace(prefix, "")
                if ym < cutoff:
                    sh.del_worksheet(ws)
