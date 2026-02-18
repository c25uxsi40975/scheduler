"""
database パッケージ
旧 database.py を分割したモジュールの再エクスポート
既存の from database import ... を維持する
"""
from database.connection import init_db, SHEET_HEADERS
from database.master import (
    get_doctors, add_doctor, update_doctor, delete_doctor,
    get_clinics, add_clinic, update_clinic, delete_clinic,
    get_affinities, set_affinity,
    get_clinic_date_overrides, set_clinic_date_override,
    set_clinic_date_overrides_batch,
)
from database.operational import (
    get_preference, get_all_preferences, upsert_preference,
    get_schedules, save_schedule, confirm_schedule,
    delete_schedule, update_schedule_assignments,
    get_all_confirmed_schedules, get_confirmed_months,
    delete_old_schedules,
)
from database.auth import (
    is_admin_password_set, set_admin_password, verify_admin_password,
    is_doctor_individual_password_set, set_doctor_individual_password,
    verify_doctor_individual_password,
    update_doctor_email, get_open_month, set_open_month,
    get_input_deadline, set_input_deadline,
)
