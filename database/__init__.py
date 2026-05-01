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
    get_double_shift_pairs, add_double_shift_pair, delete_double_shift_pair,
    set_doctor_line_user_id, get_doctor_by_line_user_id,
)
from database.operational import (
    get_preference, get_all_preferences, get_dev_preferences, upsert_preference, batch_upsert_preferences, delete_preference,
    get_schedules, save_schedule, confirm_schedule, unconfirm_schedule,
    delete_schedule, update_schedule_assignments,
    get_all_confirmed_schedules, get_confirmed_months,
    delete_old_schedules,
    has_operational_sheets,
    execute_saturday_swap, get_saturday_swap_history,
)
from database.auth import (
    is_admin_password_set, set_admin_password, verify_admin_password,
    is_dev_password_set, set_dev_password, verify_dev_password,
    is_doctor_individual_password_set, set_doctor_individual_password,
    verify_doctor_individual_password,
    get_doctor_by_account, verify_doctor_by_account,
    update_doctor_account_name,
    update_doctor_email, update_doctor_notification_settings, update_calendar_shared_emails,
    get_open_month, set_open_month,
    get_input_deadline, set_input_deadline,
    get_dev_open_month, set_dev_open_month,
    get_dev_input_deadline, set_dev_input_deadline,
    get_dev_doctor_ids, set_dev_doctor_ids,
    save_reset_code, verify_reset_code,
    save_line_linking_code, get_line_linking_code, verify_line_linking_code,
    get_doctor_email_by_account, get_doctor_id_by_account,
    clear_must_change_pw,
    # 副管理者認証
    is_subadmin_password_set, set_subadmin_password, verify_subadmin_password,
    # 平日公開設定
    get_weekday_open_section, set_weekday_open_section,
    get_weekday_deadline, set_weekday_deadline,
    # 再調整対象日
    get_weekday_readjust_dates, set_weekday_readjust_dates,
    # 平日確定済み月
    get_weekday_confirmed_months, add_weekday_confirmed_months,
    remove_weekday_confirmed_month,
    # 土曜追加/除外日付
    get_saturday_extra_dates, set_saturday_extra_dates,
    get_saturday_excluded_dates, set_saturday_excluded_dates,
    # カレンダー連携
    get_calendar_id, set_calendar_id,
    # 平日表示モード
    get_weekday_schedule_view_mode, set_weekday_schedule_view_mode,
)
from database.weekday import (
    get_weekday_configs, get_weekday_config_by_section,
    get_specimen_assignee,
    create_weekday_spreadsheet,
    add_weekday_config, update_weekday_config, delete_weekday_config,
    get_weekday_slots, add_weekday_slot, update_weekday_slot, delete_weekday_slot,
    get_weekday_slot_overrides, set_weekday_slot_overrides_batch,
    get_target_dates, get_active_target_dates,
    set_target_dates, toggle_target_date,
    get_weekday_preferences, get_weekday_preference,
    upsert_weekday_preference,
    get_weekday_schedule, batch_save_weekday_assignments,
    merge_save_weekday_assignments,
    delete_weekday_assignment,
    execute_shift_change, get_shift_change_history,
)
