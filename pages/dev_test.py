"""
開発テストページ
通知（メール・LINE）のテスト送信をダミーデータで実行する。
"""
import streamlit as st
import requests
import random
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta


# ---- ダミーデータ定義 ----

_OTHER_DUMMY_DOCTORS = [
    {"id": "D002", "name": "花子"},
    {"id": "D003", "name": "一郎"},
    {"id": "D004", "name": "美咲"},
    {"id": "D005", "name": "健太"},
    {"id": "D006", "name": "由美"},
    {"id": "D007", "name": "翔太"},
    {"id": "D008", "name": "さくら"},
]


def _get_dummy_doctors() -> list[dict]:
    """開発者情報が設定されていれば先頭に追加したダミー医員リストを返す"""
    dev_name = st.session_state.get("dev_my_name", "")
    if dev_name:
        return [{"id": "DEV0", "name": dev_name}] + _OTHER_DUMMY_DOCTORS
    return [{"id": "D001", "name": "太郎"}] + _OTHER_DUMMY_DOCTORS

DUMMY_CLINICS_SAT = [
    {"id": "C001", "name": "テスト外勤A"},
    {"id": "C002", "name": "テスト外勤B"},
    {"id": "C003", "name": "テスト外勤C"},
    {"id": "C004", "name": "テスト外勤D"},
    {"id": "C005", "name": "テスト外勤E"},
]

DUMMY_CLINICS_WD = [
    {"id": "W001", "name": "テスト平日外勤X"},
    {"id": "W002", "name": "テスト平日外勤Y"},
]

DOW_LABELS = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}
DOW_NAMES = ["月", "火", "水", "木", "金"]


def _get_saturdays(year_month: str) -> list[str]:
    """指定月の全土曜日をリストで返す"""
    y, m = map(int, year_month.split("-"))
    d = date(y, m, 1)
    end = d + relativedelta(months=1)
    saturdays = []
    while d < end:
        if d.weekday() == 5:
            saturdays.append(d.isoformat())
        d += timedelta(days=1)
    return saturdays


def _get_weekday_dates(year_month: str, days_of_week: list[int]) -> list[str]:
    """指定月の指定曜日の日付をリストで返す"""
    y, m = map(int, year_month.split("-"))
    d = date(y, m, 1)
    end = d + relativedelta(months=1)
    dates = []
    while d < end:
        if d.weekday() in days_of_week:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return dates


def _format_date_jp(date_str: str) -> str:
    """YYYY-MM-DD を M/d(曜) 形式に"""
    d = date.fromisoformat(date_str)
    dow = DOW_LABELS.get(d.weekday(), "")
    return f"{d.month}/{d.day}({dow})"


def _generate_dummy_schedule(dates: list[str], clinics: list[dict], doctors: list[dict]) -> list[dict]:
    """ダミースケジュール生成: 各日付にランダムに医員を割り当て"""
    assignments = []
    for dt in dates:
        # 各クリニックに1-2名ランダム割り当て
        available = list(doctors)
        random.shuffle(available)
        for clinic in clinics:
            if not available:
                break
            doc = available.pop(0)
            assignments.append({
                "date": dt,
                "doctor_id": doc["id"],
                "doctor_name": doc["name"],
                "clinic_id": clinic["id"],
                "clinic_name": clinic["name"],
            })
    return assignments


def _post_to_gas(action: str, payload: dict) -> dict | None:
    """GAS Web App にPOSTし、結果を返す"""
    gas_url = st.secrets.get("gas_webapp_url", "")
    if not gas_url:
        st.error("gas_webapp_url が Secrets に未設定です")
        return None
    payload["action"] = action
    try:
        resp = requests.post(gas_url, json=payload, timeout=15)
        result = resp.json()
        if result.get("status") == "ok":
            st.success(f"送信成功: {action}")
        else:
            st.error(f"送信エラー: {result.get('message', '不明')}")
        return result
    except requests.RequestException as e:
        st.error(f"通信エラー: {e}")
        return None


# ---- メインレンダリング ----

def render(dev_doctor: dict | None = None):
    # dev_doctor からログイン医員の情報を取得
    if dev_doctor:
        st.session_state["dev_my_name"] = dev_doctor.get("name", "")
        st.session_state["dev_my_email"] = dev_doctor.get("email", "")
        st.session_state["dev_my_line_id"] = dev_doctor.get("line_user_id", "")

    tab1, tab2, tab3, tab4 = st.tabs(["ダミーデータ設定", "メール通知テスト", "LINE通知テスト", "LINE希望入力テスト"])

    with tab1:
        _render_dummy_data_tab()

    with tab2:
        _render_email_test_tab()

    with tab3:
        _render_line_test_tab()

    with tab4:
        _render_line_pref_test_tab(dev_doctor)


# ---- タブ1: ダミーデータ設定 ----

def _render_dummy_data_tab():
    st.subheader("ダミーデータ設定")

    # ---- 開発者情報（マスタから自動取得） ----
    dev_name = st.session_state.get("dev_my_name", "")
    dev_email = st.session_state.get("dev_my_email", "")
    dev_line = st.session_state.get("dev_my_line_id", "")

    st.markdown("#### 開発者情報")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.text(f"名前: {dev_name or '（未取得）'}")
    with c2:
        st.text(f"メール: {dev_email or '（未設定）'}")
    with c3:
        st.text(f"LINE ID: {dev_line or '（未連携）'}")

    if not dev_name:
        st.warning("医員情報が取得できていません。再ログインしてください。")

    st.markdown("---")

    # 対象月
    today = date.today()
    month1 = today.replace(day=1)
    month2 = month1 + relativedelta(months=1)
    target_months = [month1.strftime("%Y-%m"), month2.strftime("%Y-%m")]
    st.info(f"対象月: {target_months[0]}, {target_months[1]}")

    # ---- 土曜ダミー ----
    st.markdown("#### 土曜スケジュール")
    st.caption(f"医員: {', '.join(d['name'] for d in _get_dummy_doctors())} / "
               f"外勤先: {', '.join(c['name'] for c in DUMMY_CLINICS_SAT)}")

    if st.button("土曜ダミースケジュールを生成", key="gen_sat"):
        sat_assignments = []
        for ym in target_months:
            sats = _get_saturdays(ym)
            sat_assignments.extend(
                _generate_dummy_schedule(sats, DUMMY_CLINICS_SAT, _get_dummy_doctors())
            )
        st.session_state["dev_sat_assignments"] = sat_assignments
        st.session_state["dev_sat_months"] = target_months
        st.success(f"土曜ダミースケジュールを生成しました ({len(sat_assignments)} 件)")

    # ---- 平日ダミー ----
    st.markdown("#### 平日スケジュール")

    wd_days = st.multiselect(
        "曜日を選択", DOW_NAMES, default=["月", "水"],
        key="dev_wd_days_input",
    )
    wd_section = st.text_input("セクション名", value="テストセクション", key="dev_wd_section_input")
    wd_clinic = st.text_input("外勤先名", value="テスト平日外勤X", key="dev_wd_clinic_input")

    if st.button("平日ダミースケジュールを生成", key="gen_wd"):
        dow_map = {v: i for i, v in enumerate(DOW_NAMES)}
        selected_dows = [dow_map[d] for d in wd_days if d in dow_map]
        wd_assignments = []
        wd_clinics = [{"id": "W001", "name": wd_clinic}]
        for ym in target_months:
            wd_dates = _get_weekday_dates(ym, selected_dows)
            wd_assignments.extend(
                _generate_dummy_schedule(wd_dates, wd_clinics, _get_dummy_doctors())
            )
        st.session_state["dev_wd_assignments"] = wd_assignments
        st.session_state["dev_wd_months"] = target_months
        st.session_state["dev_wd_section_val"] = wd_section
        st.session_state["dev_wd_clinic_val"] = wd_clinic
        st.session_state["dev_wd_days_val"] = wd_days
        st.success(f"平日ダミースケジュールを生成しました ({len(wd_assignments)} 件)")

    # ---- 統合プレビュー ----
    sat_data = st.session_state.get("dev_sat_assignments", [])
    wd_data = st.session_state.get("dev_wd_assignments", [])

    if sat_data or wd_data:
        st.markdown("---")
        st.markdown("#### 統合プレビュー（土曜＋平日）")

        # 全日付をソート
        all_dates = sorted(set(
            [a["date"] for a in sat_data] + [a["date"] for a in wd_data]
        ))

        # 医員ごとにグループ化
        doctor_schedule: dict[str, list[str]] = {}
        for a in sat_data + wd_data:
            name = a["doctor_name"]
            if name not in doctor_schedule:
                doctor_schedule[name] = []
            doctor_schedule[name].append(
                f"{_format_date_jp(a['date'])}: {a['clinic_name']}"
            )

        for name in sorted(doctor_schedule.keys()):
            with st.expander(f"{name} 先生"):
                for entry in sorted(doctor_schedule[name]):
                    st.text(f"  {entry}")


# ---- タブ2: メール通知テスト ----

def _render_email_test_tab():
    st.subheader("メール通知テスト")

    dev_email = st.session_state.get("dev_my_email", "")
    if "dev_test_email" not in st.session_state and dev_email:
        st.session_state["dev_test_email"] = dev_email
    test_email = st.text_input(
        "送信先メールアドレス", key="dev_test_email",
        placeholder="your-email@example.com",
    )
    if not test_email:
        st.warning("テスト送信先のメールアドレスを入力してください")
        return

    sat_data = st.session_state.get("dev_sat_assignments", [])
    wd_data = st.session_state.get("dev_wd_assignments", [])
    sat_months = st.session_state.get("dev_sat_months", [])
    wd_months = st.session_state.get("dev_wd_months", [])
    dev_name = st.session_state.get("dev_my_name", "") or "太郎"

    if not sat_data and not wd_data:
        st.info("先に「ダミーデータ設定」タブでダミースケジュールを生成してください")
        return

    # 共通ペイロード構築
    def _base_payload():
        return {
            "test_email": test_email,
            "doctor_name": dev_name,
            "doctors": [{"id": d["id"], "name": d["name"]} for d in _get_dummy_doctors()],
        }

    # ---- 土曜メール ----
    if sat_data:
        st.markdown("#### 土曜メール")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("スケジュール確定通知", key="test_sat_confirmed"):
                payload = _base_payload()
                payload["year_month"] = sat_months[0] if sat_months else ""
                payload["assignments"] = sat_data
                payload["clinics"] = [{"id": c["id"], "name": c["name"]} for c in DUMMY_CLINICS_SAT]
                _post_to_gas("test_sat_schedule_confirmed", payload)

            if st.button("希望入力確認", key="test_sat_pref"):
                dates = sorted(set(a["date"] for a in sat_data))[:4]
                summary = "\n".join(
                    f"  {_format_date_jp(d)}: ○" for d in dates
                )
                payload = _base_payload()
                payload["year_month"] = sat_months[0] if sat_months else ""
                payload["date_summary"] = summary
                payload["free_text"] = "テスト備考コメント"
                _post_to_gas("test_sat_preference_confirmed", payload)

            if st.button("全員入力完了", key="test_sat_all"):
                payload = _base_payload()
                payload["year_month"] = sat_months[0] if sat_months else ""
                payload["doctor_count"] = len(_get_dummy_doctors())
                _post_to_gas("test_sat_all_complete", payload)

        with col2:
            if st.button("入力期限リマインダー", key="test_sat_deadline"):
                payload = _base_payload()
                payload["year_month"] = sat_months[0] if sat_months else ""
                payload["deadline"] = (date.today() + timedelta(days=3)).isoformat()
                payload["submitted"] = False
                _post_to_gas("test_sat_deadline_reminder", payload)

            if st.button("期限超過通知", key="test_sat_overdue"):
                payload = _base_payload()
                payload["year_month"] = sat_months[0] if sat_months else ""
                payload["missing_names"] = ["一郎", "美咲", "翔太"]
                payload["total_count"] = len(_get_dummy_doctors())
                _post_to_gas("test_sat_deadline_overdue", payload)

            if st.button("金曜リマインダー", key="test_sat_friday"):
                # 翌日分のダミー
                tomorrow = (date.today() + timedelta(days=1)).isoformat()
                payload = _base_payload()
                payload["date"] = tomorrow
                payload["clinic_name"] = DUMMY_CLINICS_SAT[0]["name"]
                _post_to_gas("test_sat_friday_reminder", payload)

        if st.button("パスワードリセット", key="test_pw_reset"):
            payload = _base_payload()
            payload["account_name"] = "test_account"
            payload["reset_code"] = "123456"
            _post_to_gas("test_password_reset", payload)

    # ---- 平日メール ----
    if wd_data:
        st.markdown("---")
        st.markdown("#### 平日メール")
        wd_section = st.session_state.get("dev_wd_section_val", "テストセクション")
        wd_clinic = st.session_state.get("dev_wd_clinic_val", "テスト平日外勤X")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("スケジュール確定通知", key="test_wd_confirmed"):
                payload = _base_payload()
                payload["section"] = wd_section
                payload["clinic_name"] = wd_clinic
                payload["year_months"] = wd_months
                payload["assignments"] = wd_data
                _post_to_gas("test_wd_schedule_confirmed", payload)

            if st.button("希望入力確認", key="test_wd_pref"):
                dates = sorted(set(a["date"] for a in wd_data))[:4]
                summary = "\n".join(f"  {_format_date_jp(d)}: ○" for d in dates)
                payload = _base_payload()
                payload["clinic_name"] = wd_clinic
                payload["date_summary"] = summary
                payload["free_text"] = "テスト備考"
                _post_to_gas("test_wd_preference_confirmed", payload)

            if st.button("全員入力完了", key="test_wd_all"):
                payload = _base_payload()
                payload["section"] = wd_section
                payload["clinic_name"] = wd_clinic
                payload["doctor_count"] = len(_get_dummy_doctors())
                _post_to_gas("test_wd_all_complete", payload)

            if st.button("シフト変更通知", key="test_wd_change"):
                payload = _base_payload()
                payload["section"] = wd_section
                payload["clinic_name"] = wd_clinic
                payload["year_month"] = wd_data[0]["date"][:7] if wd_data else ""
                payload["date"] = wd_data[0]["date"] if wd_data else ""
                payload["slot_name"] = "スロットA"
                payload["actor_name"] = dev_name
                payload["original_doctor_name"] = dev_name
                payload["original_doctor_email"] = ""
                payload["new_doctor_name"] = "花子"
                payload["new_doctor_email"] = ""
                _post_to_gas("test_wd_shift_change", payload)

        with col2:
            if st.button("入力期限リマインダー", key="test_wd_deadline"):
                payload = _base_payload()
                payload["section"] = wd_section
                payload["clinic_name"] = wd_clinic
                payload["deadline"] = (date.today() + timedelta(days=3)).isoformat()
                payload["submitted"] = False
                _post_to_gas("test_wd_deadline_reminder", payload)

            if st.button("期限超過通知", key="test_wd_overdue"):
                payload = _base_payload()
                payload["section"] = wd_section
                payload["clinic_name"] = wd_clinic
                payload["missing_names"] = ["一郎", "美咲"]
                payload["total_count"] = len(_get_dummy_doctors())
                _post_to_gas("test_wd_deadline_overdue", payload)

            if st.button("前日リマインダー", key="test_wd_daybefore"):
                tomorrow = (date.today() + timedelta(days=1)).isoformat()
                payload = _base_payload()
                payload["date"] = tomorrow
                payload["clinic_name"] = wd_clinic
                payload["slot_name"] = "スロットA"
                _post_to_gas("test_wd_day_before_reminder", payload)

            if st.button("再調整希望入力依頼", key="test_wd_readjust_req"):
                payload = _base_payload()
                payload["section"] = wd_section
                payload["clinic_name"] = wd_clinic
                payload["deadline"] = (date.today() + timedelta(days=5)).isoformat()
                payload["target_date_count"] = 3
                payload["mode"] = "fill"
                _post_to_gas("test_wd_readjust_request", payload)

        if st.button("再調整完了通知", key="test_wd_readjusted"):
            payload = _base_payload()
            payload["section"] = wd_section
            payload["clinic_name"] = wd_clinic
            payload["year_months"] = wd_months
            payload["assignments"] = wd_data
            payload["mode"] = "fill"
            payload["period"] = f"{wd_months[0]}〜{wd_months[-1]}" if wd_months else ""
            _post_to_gas("test_wd_readjusted", payload)

    # ---- 統合メール ----
    if sat_data and wd_data:
        st.markdown("---")
        st.markdown("#### 統合メール")
        if st.button("週間統合スケジュール通知", key="test_weekly"):
            payload = _base_payload()
            payload["sat_assignments"] = sat_data
            payload["wd_assignments"] = wd_data
            payload["sat_clinics"] = [{"id": c["id"], "name": c["name"]} for c in DUMMY_CLINICS_SAT]
            payload["wd_clinic_name"] = st.session_state.get("dev_wd_clinic_val", "テスト平日外勤X")
            payload["year_months"] = sat_months
            _post_to_gas("test_weekly_integrated", payload)


# ---- タブ3: LINE通知テスト ----

def _render_line_test_tab():
    st.subheader("LINE通知テスト")

    # LINE Push残数表示
    _show_line_quota()

    dev_line = st.session_state.get("dev_my_line_id", "")
    if "dev_line_user_id" not in st.session_state and dev_line:
        st.session_state["dev_line_user_id"] = dev_line
    line_user_id = st.text_input(
        "LINE User ID", key="dev_line_user_id",
        placeholder="Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    )
    if not line_user_id:
        st.warning("テスト送信先の LINE User ID を入力してください")
        return

    sat_data = st.session_state.get("dev_sat_assignments", [])
    wd_data = st.session_state.get("dev_wd_assignments", [])
    sat_months = st.session_state.get("dev_sat_months", [])
    dev_name = st.session_state.get("dev_my_name", "") or "太郎"

    if not sat_data and not wd_data:
        st.info("先に「ダミーデータ設定」タブでダミースケジュールを生成してください")
        return

    def _base_payload():
        return {
            "line_user_id": line_user_id,
            "doctor_name": dev_name,
            "doctors": [{"id": d["id"], "name": d["name"]} for d in _get_dummy_doctors()],
        }

    # ---- 土曜LINE ----
    if sat_data:
        st.markdown("#### 土曜LINE")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("スケジュール確定＋画像 (2通)", key="test_line_sat_confirmed"):
                # 本番と同じ流れ: 画像生成 → Drive → GAS に URL 付きで送信
                schedule_image_url = None
                try:
                    from components.schedule_image import generate_schedule_image
                    from database.drive_utils import upload_schedule_image
                    target_ym = sat_months[0] if sat_months else ""
                    dummy_sched = {"assignments": [
                        {"date": a["date"], "doctor_id": a["doctor_id"], "clinic_id": a["clinic_id"]}
                        for a in sat_data if a["date"].startswith(target_ym)
                    ]}
                    dummy_doctors = _get_dummy_doctors()
                    png_bytes = generate_schedule_image(
                        dummy_sched, dummy_doctors, DUMMY_CLINICS_SAT,
                        sat_months[0] if sat_months else "",
                    )
                    if png_bytes:
                        file_id = upload_schedule_image(png_bytes, "dev_test_schedule.png")
                        if file_id:
                            schedule_image_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                            st.info(f"画像アップロード完了 (file_id={file_id})")
                        else:
                            st.warning("画像のDriveアップロードに失敗（テキストのみ送信）")
                    else:
                        st.warning("画像生成に失敗（テキストのみ送信）")
                except Exception as e:
                    st.warning(f"画像処理エラー: {e}（テキストのみ送信）")

                payload = _base_payload()
                payload["year_month"] = sat_months[0] if sat_months else ""
                payload["assignments"] = sat_data
                payload["clinics"] = [{"id": c["id"], "name": c["name"]} for c in DUMMY_CLINICS_SAT]
                if schedule_image_url:
                    payload["schedule_image_url"] = schedule_image_url
                _post_to_gas("test_line_sat_schedule_confirmed", payload)

        with col2:
            if st.button("希望入力リマインダー (1通)", key="test_line_sat_deadline"):
                payload = _base_payload()
                payload["year_month"] = sat_months[0] if sat_months else ""
                payload["submitted"] = False
                _post_to_gas("test_line_sat_deadline_reminder", payload)

        with col3:
            if st.button("金曜リマインダー (1通)", key="test_line_sat_friday"):
                tomorrow = (date.today() + timedelta(days=1)).isoformat()
                payload = _base_payload()
                payload["date"] = tomorrow
                payload["clinic_name"] = DUMMY_CLINICS_SAT[0]["name"]
                _post_to_gas("test_line_sat_friday_reminder", payload)

    # ---- 平日LINE ----
    if wd_data:
        st.markdown("#### 平日LINE")
        wd_clinic = st.session_state.get("dev_wd_clinic_val", "テスト平日外勤X")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("スケジュール確定 (1通)", key="test_line_wd_confirmed"):
                payload = _base_payload()
                payload["clinic_name"] = wd_clinic
                payload["assignments"] = wd_data
                payload["year_months"] = st.session_state.get("dev_wd_months", [])
                _post_to_gas("test_line_wd_schedule_confirmed", payload)

        with col2:
            if st.button("前日リマインダー (1通)", key="test_line_wd_daybefore"):
                tomorrow = (date.today() + timedelta(days=1)).isoformat()
                payload = _base_payload()
                payload["date"] = tomorrow
                payload["clinic_name"] = wd_clinic
                payload["slot_name"] = "スロットA"
                _post_to_gas("test_line_wd_day_before_reminder", payload)

    # ---- 統合LINE ----
    if sat_data and wd_data:
        st.markdown("#### 統合LINE")
        if st.button("週間統合スケジュール (1通)", key="test_line_weekly"):
            payload = _base_payload()
            payload["sat_assignments"] = sat_data
            payload["wd_assignments"] = wd_data
            payload["sat_clinics"] = [{"id": c["id"], "name": c["name"]} for c in DUMMY_CLINICS_SAT]
            payload["wd_clinic_name"] = st.session_state.get("dev_wd_clinic_val", "テスト平日外勤X")
            payload["year_months"] = sat_months
            _post_to_gas("test_line_weekly_integrated", payload)


def _show_line_quota():
    """LINE Push API 残数を表示"""
    if st.button("LINE Push残数を確認", key="check_line_quota"):
        result = _post_to_gas("get_line_quota", {})
        if result and result.get("status") == "ok":
            used = result.get("totalUsage", "?")
            st.info(f"当月LINE Push消費数: {used} / 200")
        else:
            st.warning("LINE Push残数の取得に失敗しました")


# ---- タブ4: LINE希望入力テスト ----

def _render_line_pref_test_tab(dev_doctor: dict | None):
    from database import (
        get_dev_open_month, set_dev_open_month,
        get_dev_input_deadline, set_dev_input_deadline,
        get_dev_doctor_ids, set_dev_doctor_ids,
        get_dev_preferences, delete_preference,
    )
    from database.connection import _clear_data_cache
    from optimizer import get_target_saturdays

    st.subheader("LINE希望入力テスト")

    if not dev_doctor:
        st.warning("医員アカウントでログインしてください")
        return

    doctor_id = dev_doctor["id"]
    doctor_name = dev_doctor.get("name", "")

    # ---- 公開設定 ----
    st.markdown("#### 希望入力 公開設定")
    st.caption("開発者のみに希望入力を公開します（他の医員には影響しません）")

    current_dev_month = get_dev_open_month()
    current_dev_deadline = get_dev_input_deadline()
    current_dev_ids = get_dev_doctor_ids()

    if current_dev_month:
        is_me = doctor_id in current_dev_ids
        st.info(f"現在の公開月: **{current_dev_month}**　期限: {current_dev_deadline or '未設定'}　"
                f"対象: {current_dev_ids}{'（自分を含む）' if is_me else ''}")
    else:
        st.info("現在の開発者テスト公開: なし")

    today = date.today()
    month_options = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(3)]
    col_month, col_deadline = st.columns(2)
    with col_month:
        target_month = st.selectbox("対象月", month_options, key="dev_pref_month")
    with col_deadline:
        deadline_date = st.date_input(
            "期限日", value=today + timedelta(days=14), key="dev_pref_deadline",
        )

    col_open, col_close = st.columns(2)
    with col_open:
        if st.button("公開する", type="primary", key="dev_pref_open"):
            set_dev_open_month(target_month)
            set_dev_input_deadline(deadline_date.isoformat())
            # 開発者IDリストに自分を追加
            ids = get_dev_doctor_ids()
            if doctor_id not in ids:
                ids.append(doctor_id)
            set_dev_doctor_ids(ids)
            _clear_data_cache()
            # LINEで希望入力を促す通知を送信
            dev_line_id = dev_doctor.get("line_user_id", "")
            if dev_line_id:
                month_label = target_month.replace("-", "年") + "月"
                _post_to_gas("test_line_push_text", {
                    "line_user_id": dev_line_id,
                    "text": f"【開発テスト】{month_label} の希望入力が公開されました。\n\nメニューの「希望入力」から入力してください。",
                })
            st.success(f"{target_month} を開発者テスト用に公開しました")
            st.rerun()
    with col_close:
        if st.button("閉じる", key="dev_pref_close"):
            set_dev_open_month(None)
            set_dev_input_deadline(None)
            set_dev_doctor_ids([])
            _clear_data_cache()
            st.success("開発者テスト公開を終了しました")
            st.rerun()

    st.markdown("---")

    # ---- 保存された希望データ確認 ----
    st.markdown("#### 保存された希望データ")

    view_month = current_dev_month or (month_options[0] if month_options else "")
    if not view_month:
        st.info("公開月を設定してください")
        return

    if st.button("データを更新", key="dev_pref_refresh"):
        _clear_data_cache()
        # ワークシートキャッシュもクリア
        from database.connection import _ws_cache_operational
        _ws_cache_operational.clear()
        st.rerun()

    try:
        y, m = map(int, view_month.split("-"))
        saturdays = get_target_saturdays(y, m)
    except Exception:
        saturdays = []

    prefs = get_dev_preferences(view_month)
    if not prefs:
        st.info(f"{view_month} の希望データはまだありません。LINEから「希望入力」を試してください。")
    else:
        # テーブル表示
        def _to_date(s):
            return s if isinstance(s, date) else date.fromisoformat(str(s))
        sat_dates = [_to_date(s) for s in saturdays]
        sat_strs = [s.isoformat() for s in sat_dates]
        header_cols = ["医員名"] + [f"{s.month}/{s.day}" for s in sat_dates] + ["備考", "更新日時"]
        rows = []
        for p in prefs:
            ng = set(p.get("ng_dates") or [])
            avoid = set(p.get("avoid_dates") or [])
            post_night = set(p.get("post_night_dates") or [])
            row = [p.get("doctor_name", "?")]
            for ds in sat_strs:
                if ds in ng:
                    row.append("×")
                elif ds in avoid:
                    row.append("△")
                elif ds in post_night:
                    row.append("当○")
                else:
                    row.append("○")
            row.append(p.get("free_text", ""))
            row.append(p.get("updated_at", ""))
            rows.append(row)

        import pandas as pd
        df = pd.DataFrame(rows, columns=header_cols)
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ---- テストデータ削除 ----
    st.markdown("#### テストデータ削除")
    if st.button(f"自分（{doctor_name}）の希望データを削除", key="dev_pref_delete"):
        deleted = delete_preference(doctor_id, view_month, sheet_prefix="dev_")
        _clear_data_cache()
        if deleted:
            st.success(f"{doctor_name} の {view_month} の希望データを削除しました")
        else:
            st.info("削除対象のデータが見つかりませんでした")
        st.rerun()
