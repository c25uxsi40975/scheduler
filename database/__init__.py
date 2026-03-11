"""
database パッケージ
旧 database.py を分割したモジュールの再エクスポート
既存の from database import ... を維持する
"""
from database.connection import init_db, SHEET_HEADERS
from database.master import (
    get_doctors, add_doctor, update_doctor, delete_doctor,
    get_clinics, add_clinic, update_clinic, delete_clinic,
    get_affinities, set_affinity, batch_set_affinities,
    batch_update_max_assignments,
    get_clinic_date_overrides, set_clinic_date_override,
    set_clinic_date_overrides_batch,
    get_training_data, append_training_data,
    get_suitability_training_data, append_suitability_training_data,
)
from database.operational import (
    get_preference, get_all_preferences, upsert_preference, batch_upsert_preferences,
    get_schedules, save_schedule, confirm_schedule,
    delete_schedule, update_schedule_assignments,
    get_all_confirmed_schedules, get_confirmed_months,
    delete_old_schedules,
)
from database.auth import (
    is_admin_password_set, set_admin_password, verify_admin_password,
    is_doctor_individual_password_set, set_doctor_individual_password,
    verify_doctor_individual_password,
    get_doctor_by_account, verify_doctor_by_account,
    update_doctor_account_name,
    update_doctor_email, get_open_month, set_open_month,
    get_input_deadline, set_input_deadline,
    save_reset_code, verify_reset_code,
    get_doctor_email_by_account, get_doctor_id_by_account,
    clear_must_change_pw,
    # 副管理者認証
    is_subadmin_password_set, set_subadmin_password, verify_subadmin_password,
    # 平日公開設定
    get_weekday_open_section, set_weekday_open_section,
    get_weekday_deadline, set_weekday_deadline,
    # 土曜追加/除外日付
    get_saturday_extra_dates, set_saturday_extra_dates,
    get_saturday_excluded_dates, set_saturday_excluded_dates,
)
from database.weekday import (
    get_weekday_configs, get_weekday_config_by_section,
    add_weekday_config, update_weekday_config, delete_weekday_config,
    get_weekday_slots, add_weekday_slot, update_weekday_slot, delete_weekday_slot,
    get_weekday_slot_overrides, set_weekday_slot_overrides_batch,
    get_target_dates, get_active_target_dates,
    set_target_dates, toggle_target_date,
    get_weekday_preferences, get_weekday_preference,
    upsert_weekday_preference,
    get_weekday_schedule, batch_save_weekday_assignments,
    delete_weekday_assignment,
    execute_swap, get_swap_history,
)
