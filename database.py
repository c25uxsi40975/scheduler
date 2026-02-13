"""
データベース管理モジュール
SQLiteで医員・外勤先・希望・スケジュールを永続化
"""
import sqlite3
import json
import os
import hashlib
from datetime import datetime, date
from contextlib import contextmanager

DB_PATH = os.environ.get("GAKIN_DB_PATH", "gakin.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        -- マイグレーション: avoid_dates カラム追加
        -- (既存DBとの互換性のため)
        """)
        try:
            conn.execute("ALTER TABLE preferences ADD COLUMN avoid_dates TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass  # カラム既存
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            fee INTEGER NOT NULL DEFAULT 0,
            frequency TEXT NOT NULL DEFAULT 'weekly',
            preferred_doctors TEXT DEFAULT '[]',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL,
            year_month TEXT NOT NULL,
            ng_dates TEXT DEFAULT '[]',
            avoid_dates TEXT DEFAULT '[]',
            preferred_clinics TEXT DEFAULT '[]',
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (doctor_id) REFERENCES doctors(id),
            UNIQUE(doctor_id, year_month)
        );

        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year_month TEXT NOT NULL,
            plan_name TEXT NOT NULL,
            assignments TEXT NOT NULL,
            total_variance REAL DEFAULT 0,
            satisfaction_score REAL DEFAULT 0,
            is_confirmed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(year_month, plan_name)
        );

        CREATE TABLE IF NOT EXISTS doctor_clinic_affinity (
            doctor_id INTEGER NOT NULL,
            clinic_id INTEGER NOT NULL,
            weight REAL DEFAULT 1.0,
            PRIMARY KEY (doctor_id, clinic_id),
            FOREIGN KEY (doctor_id) REFERENCES doctors(id),
            FOREIGN KEY (clinic_id) REFERENCES clinics(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clinic_date_overrides (
            clinic_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            required_doctors INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (clinic_id, date),
            FOREIGN KEY (clinic_id) REFERENCES clinics(id)
        );
        """)


# ---- Doctor CRUD ----

def get_doctors(active_only=True):
    with get_conn() as conn:
        if active_only:
            rows = conn.execute("SELECT * FROM doctors WHERE is_active=1 ORDER BY name").fetchall()
        else:
            rows = conn.execute("SELECT * FROM doctors ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def add_doctor(name):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO doctors (name) VALUES (?)", (name,))


def update_doctor(doc_id, name=None, is_active=None):
    with get_conn() as conn:
        if name is not None:
            conn.execute("UPDATE doctors SET name=? WHERE id=?", (name, doc_id))
        if is_active is not None:
            conn.execute("UPDATE doctors SET is_active=? WHERE id=?", (is_active, doc_id))


def delete_doctor(doc_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM doctor_clinic_affinity WHERE doctor_id=?", (doc_id,))
        conn.execute("DELETE FROM preferences WHERE doctor_id=?", (doc_id,))
        conn.execute("DELETE FROM doctors WHERE id=?", (doc_id,))


# ---- Clinic CRUD ----

def get_clinics(active_only=True):
    with get_conn() as conn:
        if active_only:
            rows = conn.execute("SELECT * FROM clinics WHERE is_active=1 ORDER BY name").fetchall()
        else:
            rows = conn.execute("SELECT * FROM clinics ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def add_clinic(name, fee=0, frequency="weekly", preferred_doctors=None):
    with get_conn() as conn:
        pref = json.dumps(preferred_doctors or [])
        conn.execute(
            "INSERT OR IGNORE INTO clinics (name, fee, frequency, preferred_doctors) VALUES (?,?,?,?)",
            (name, fee, frequency, pref)
        )


def update_clinic(clinic_id, **kwargs):
    with get_conn() as conn:
        for key, val in kwargs.items():
            if key == "preferred_doctors":
                val = json.dumps(val)
            conn.execute(f"UPDATE clinics SET {key}=? WHERE id=?", (val, clinic_id))


# ---- Preferences ----

def get_preference(doctor_id, year_month):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM preferences WHERE doctor_id=? AND year_month=?",
            (doctor_id, year_month)
        ).fetchone()
        if row:
            d = dict(row)
            d["ng_dates"] = json.loads(d["ng_dates"])
            d["avoid_dates"] = json.loads(d.get("avoid_dates") or "[]")
            d["preferred_clinics"] = json.loads(d["preferred_clinics"])
            return d
        return None


def get_all_preferences(year_month):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.*, d.name as doctor_name FROM preferences p "
            "JOIN doctors d ON p.doctor_id = d.id "
            "WHERE p.year_month=?",
            (year_month,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["ng_dates"] = json.loads(d["ng_dates"])
            d["avoid_dates"] = json.loads(d.get("avoid_dates") or "[]")
            d["preferred_clinics"] = json.loads(d["preferred_clinics"])
            result.append(d)
        return result


def upsert_preference(doctor_id, year_month, ng_dates=None, avoid_dates=None, preferred_clinics=None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO preferences (doctor_id, year_month, ng_dates, avoid_dates, preferred_clinics, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(doctor_id, year_month) DO UPDATE SET
                ng_dates=excluded.ng_dates,
                avoid_dates=excluded.avoid_dates,
                preferred_clinics=excluded.preferred_clinics,
                updated_at=excluded.updated_at
        """, (
            doctor_id, year_month,
            json.dumps(ng_dates or []),
            json.dumps(avoid_dates or []),
            json.dumps(preferred_clinics or []),
        ))


# ---- Affinity ----

def get_affinities():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.*, d.name as doctor_name, c.name as clinic_name "
            "FROM doctor_clinic_affinity a "
            "JOIN doctors d ON a.doctor_id = d.id "
            "JOIN clinics c ON a.clinic_id = c.id"
        ).fetchall()
        return [dict(r) for r in rows]


def set_affinity(doctor_id, clinic_id, weight):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO doctor_clinic_affinity (doctor_id, clinic_id, weight)
            VALUES (?, ?, ?)
            ON CONFLICT(doctor_id, clinic_id) DO UPDATE SET weight=excluded.weight
        """, (doctor_id, clinic_id, weight))


# ---- Schedules ----

def save_schedule(year_month, plan_name, assignments, total_variance=0, satisfaction_score=0):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO schedules (year_month, plan_name, assignments, total_variance, satisfaction_score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(year_month, plan_name) DO UPDATE SET
                assignments=excluded.assignments,
                total_variance=excluded.total_variance,
                satisfaction_score=excluded.satisfaction_score,
                created_at=datetime('now')
        """, (year_month, plan_name, json.dumps(assignments), total_variance, satisfaction_score))


def get_schedules(year_month):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE year_month=? ORDER BY created_at DESC",
            (year_month,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["assignments"] = json.loads(d["assignments"])
            result.append(d)
        return result


def confirm_schedule(schedule_id):
    with get_conn() as conn:
        row = conn.execute("SELECT year_month FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if row:
            conn.execute("UPDATE schedules SET is_confirmed=0 WHERE year_month=?", (row["year_month"],))
            conn.execute("UPDATE schedules SET is_confirmed=1 WHERE id=?", (schedule_id,))


def delete_schedule(schedule_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def update_schedule_assignments(schedule_id, assignments):
    with get_conn() as conn:
        conn.execute(
            "UPDATE schedules SET assignments=?, created_at=datetime('now') WHERE id=?",
            (json.dumps(assignments), schedule_id)
        )


def get_all_confirmed_schedules():
    """全月の確定スケジュールを取得（累計報酬計算用）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE is_confirmed=1 ORDER BY year_month"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["assignments"] = json.loads(d["assignments"])
            result.append(d)
        return result


# ---- Settings / Auth ----

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def is_admin_password_set() -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='admin_password'"
        ).fetchone()
        return row is not None


def set_admin_password(password: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO settings (key, value) VALUES ('admin_password', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (_hash_password(password),))


def verify_admin_password(password: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='admin_password'"
        ).fetchone()
        if not row:
            return False
        return row["value"] == _hash_password(password)


def is_doctor_password_set() -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='doctor_password'"
        ).fetchone()
        return row is not None


def set_doctor_password(password: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO settings (key, value) VALUES ('doctor_password', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (_hash_password(password),))


def verify_doctor_password(password: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='doctor_password'"
        ).fetchone()
        if not row:
            return False
        return row["value"] == _hash_password(password)


# ---- Clinic Date Overrides ----

def get_clinic_date_overrides(year_month):
    """指定月のオーバーライドを {(clinic_id, date_str): required_doctors} で返す"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clinic_date_overrides WHERE date LIKE ?",
            (year_month + "%",)
        ).fetchall()
        return {(r["clinic_id"], r["date"]): r["required_doctors"] for r in rows}


def set_clinic_date_override(clinic_id, date_str, required_doctors):
    with get_conn() as conn:
        if required_doctors == 1:
            conn.execute(
                "DELETE FROM clinic_date_overrides WHERE clinic_id=? AND date=?",
                (clinic_id, date_str)
            )
        else:
            conn.execute("""
                INSERT INTO clinic_date_overrides (clinic_id, date, required_doctors)
                VALUES (?, ?, ?)
                ON CONFLICT(clinic_id, date) DO UPDATE SET required_doctors=excluded.required_doctors
            """, (clinic_id, date_str, required_doctors))


def delete_clinic(clinic_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM doctor_clinic_affinity WHERE clinic_id=?", (clinic_id,))
        conn.execute("DELETE FROM clinic_date_overrides WHERE clinic_id=?", (clinic_id,))
        conn.execute("DELETE FROM clinics WHERE id=?", (clinic_id,))


def delete_old_schedules(months_to_keep=4):
    """4ヶ月より古いデータを削除"""
    from dateutil.relativedelta import relativedelta
    cutoff = (datetime.now() - relativedelta(months=months_to_keep)).strftime("%Y-%m")
    with get_conn() as conn:
        conn.execute("DELETE FROM schedules WHERE year_month < ?", (cutoff,))
        conn.execute("DELETE FROM preferences WHERE year_month < ?", (cutoff,))
