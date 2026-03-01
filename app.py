"""
外勤調整システム - メインアプリケーション
Streamlit ベースの Web アプリ
"""
import streamlit as st
from datetime import date
from dateutil.relativedelta import relativedelta

import requests
from database import (
    init_db, get_doctors,
    is_admin_password_set, set_admin_password, verify_admin_password,
    is_doctor_individual_password_set, set_doctor_individual_password,
    verify_doctor_individual_password, verify_doctor_by_account,
    update_doctor_email, update_doctor_account_name,
    get_open_month, set_open_month, get_input_deadline, set_input_deadline,
    get_confirmed_months,
    save_reset_code, verify_reset_code,
    get_doctor_email_by_account, get_doctor_id_by_account,
)
from optimizer import get_target_saturdays
from security import (
    check_rate_limit, record_failed_attempt, reset_rate_limit,
    generate_reset_code, validate_password, validate_email,
)
from audit import log_event
from pages import (
    admin_master, admin_preferences, admin_generate,
    admin_schedule, doctor_input, doctor_schedule,
)

# ---- 初期設定 ----
st.set_page_config(
    page_title="外勤調整システム",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# サイドバーを完全に非表示
st.markdown(
    "<style>[data-testid='stSidebar']{display:none}</style>",
    unsafe_allow_html=True,
)

# 2スプレッドシート構成の必須チェック
_missing = []
if not st.secrets.get("spreadsheet_key", ""):
    _missing.append("spreadsheet_key")
if not st.secrets.get("spreadsheet_key_operational", ""):
    _missing.append("spreadsheet_key_operational")
if _missing:
    st.error(
        f"Secrets に以下のキーが未設定です: {', '.join(_missing)}\n\n"
        "マスタ用 (spreadsheet_key) と運用データ用 (spreadsheet_key_operational) の"
        "2つのスプレッドシートキーが必要です。"
    )
    st.stop()

init_db()

# ---- セッション状態初期化 ----
if "role" not in st.session_state:
    st.session_state.role = None
if "admin_authenticated" not in st.session_state:
    st.session_state.admin_authenticated = False
if "doctor_id" not in st.session_state:
    st.session_state.doctor_id = None
if "doctor_authenticated" not in st.session_state:
    st.session_state.doctor_authenticated = False

# ---- セッションタイムアウト（1時間） ----
import time as _time
_SESSION_TIMEOUT = 3600

def _check_session_timeout():
    """非活動1時間でセッションをタイムアウト"""
    now = _time.time()
    last = st.session_state.get("_last_activity", now)
    if now - last > _SESSION_TIMEOUT and st.session_state.get("role"):
        st.session_state.role = None
        st.session_state.admin_authenticated = False
        st.session_state.doctor_authenticated = False
        st.session_state.doctor_id = None
        st.warning("セッションがタイムアウトしました。再度ログインしてください。")
        st.stop()
    st.session_state["_last_activity"] = now

_check_session_timeout()


def _show_role_selection():
    """ロール選択画面"""
    st.title("外勤調整システム")
    st.markdown("---")

    if st.button("管理者としてログイン", use_container_width=True, type="primary"):
        st.session_state.role = "admin"
        st.rerun()
    if st.button("医員としてログイン", use_container_width=True, type="primary"):
        st.session_state.role = "doctor"
        st.rerun()


def _show_admin_login():
    """管理者パスワード認証画面"""
    st.title("管理者ログイン")
    st.markdown("---")

    if not is_admin_password_set():
        st.info("管理者パスワードが未設定です。初回セットアップを行います。")
        setup_token_input = st.text_input(
            "セットアップトークン", type="password", key="setup_token",
            help="Streamlit Secretsに設定された setup_token を入力してください",
        )
        pw1 = st.text_input("パスワード", type="password", key="pw_new1")
        pw2 = st.text_input("パスワード（確認）", type="password", key="pw_new2")
        if st.button("パスワードを設定", type="primary"):
            import hmac
            expected_token = st.secrets.get("setup_token", "")
            if not expected_token:
                st.error("setup_token が Secrets に未設定です。管理者に連絡してください。")
            elif not hmac.compare_digest(setup_token_input, expected_token):
                st.error("セットアップトークンが正しくありません")
            elif not pw1:
                st.error("パスワードを入力してください")
            elif pw1 != pw2:
                st.error("パスワードが一致しません")
            else:
                pw_ok, pw_msg = validate_password(pw1)
                if not pw_ok:
                    st.error(pw_msg)
                else:
                    set_admin_password(pw1)
                    log_event("admin_password_set", "admin", "初回セットアップ")
                    st.session_state.admin_authenticated = True
                    st.success("パスワードを設定しました")
                    st.rerun()
    else:
        allowed, remaining = check_rate_limit("admin")
        if not allowed:
            st.error(f"ログイン試行回数の上限に達しました。{remaining}秒後にお試しください。")
        else:
            pw = st.text_input("パスワード", type="password", key="pw_login")
            if st.button("ログイン", type="primary"):
                if verify_admin_password(pw):
                    reset_rate_limit("admin")
                    log_event("admin_login_success", "admin")
                    st.session_state.admin_authenticated = True
                    st.rerun()
                else:
                    record_failed_attempt("admin")
                    log_event("admin_login_failed", "admin")
                    st.error("パスワードが正しくありません")

    st.markdown("---")
    if st.button("← 戻る"):
        st.session_state.role = None
        st.rerun()


def _show_password_reset():
    """医員パスワードリセット画面"""
    st.subheader("パスワードリセット")

    step = st.session_state.get("_pw_reset_step", "account")

    if step == "account":
        account = st.text_input("アカウント名を入力", key="reset_account")
        if st.button("リセットコードを送信", type="primary"):
            if not account.strip():
                st.error("アカウント名を入力してください")
            else:
                email = get_doctor_email_by_account(account.strip())
                if email:
                    code = generate_reset_code()
                    save_reset_code(account.strip(), code)
                    # GAS webhook でリセットコードをメール送信
                    gas_url = st.secrets.get("gas_webapp_url", "")
                    if gas_url:
                        try:
                            requests.post(gas_url, json={
                                "action": "password_reset_code",
                                "account_name": account.strip(),
                                "doctor_email": email,
                                "reset_code": code,
                            }, timeout=10)
                        except requests.RequestException:
                            pass
                    log_event("password_reset_requested", account.strip(), "リセットコード送信")
                    st.session_state._pw_reset_step = "code"
                    st.session_state._pw_reset_account = account.strip()
                    st.success("リセットコードをメールに送信しました")
                    st.rerun()
                else:
                    st.warning("メールアドレスが設定されていないアカウントです。管理者にお問い合わせください。")

    elif step == "code":
        account = st.session_state.get("_pw_reset_account", "")
        st.info(f"アカウント「{account}」に紐づくメールアドレスにリセットコードを送信しました。")
        code_input = st.text_input("リセットコード（6桁）", key="reset_code_input")
        new_pw1 = st.text_input("新しいパスワード", type="password", key="reset_pw1")
        new_pw2 = st.text_input("新しいパスワード（確認）", type="password", key="reset_pw2")
        if st.button("パスワードを変更", type="primary"):
            if not code_input.strip():
                st.error("リセットコードを入力してください")
            elif not new_pw1:
                st.error("新しいパスワードを入力してください")
            elif new_pw1 != new_pw2:
                st.error("パスワードが一致しません")
            else:
                # パスワードポリシーはコード消費前に検証
                pw_ok, pw_msg = validate_password(new_pw1)
                if not pw_ok:
                    st.error(pw_msg)
                elif not verify_reset_code(account, code_input.strip()):
                    st.error("リセットコードが正しくないか、期限切れです")
                else:
                    doc_id = get_doctor_id_by_account(account)
                    if doc_id:
                        set_doctor_individual_password(doc_id, new_pw1)
                        log_event("password_reset_completed", account, "メール経由リセット")
                        st.success("パスワードを変更しました。ログインしてください。")
                        st.session_state.pop("_pw_reset_mode", None)
                        st.session_state.pop("_pw_reset_step", None)
                        st.session_state.pop("_pw_reset_account", None)
                        st.rerun()

    if st.button("← ログイン画面に戻る"):
        st.session_state.pop("_pw_reset_mode", None)
        st.session_state.pop("_pw_reset_step", None)
        st.session_state.pop("_pw_reset_account", None)
        st.rerun()


def _show_doctor_login():
    """医員ログイン画面（アカウント＋パスワード入力）"""
    st.title("医員ログイン")
    st.markdown("---")

    # パスワードリセットモード
    if st.session_state.get("_pw_reset_mode"):
        _show_password_reset()
        return

    allowed, remaining = check_rate_limit("doctor")
    if not allowed:
        st.error(f"ログイン試行回数の上限に達しました。{remaining}秒後にお試しください。")
    else:
        account = st.text_input("アカウント名", key="doc_account_login")
        pw = st.text_input("パスワード", type="password", key="doc_pw_login")

        if st.button("ログイン", type="primary"):
            if not account:
                st.error("アカウント名を入力してください")
            elif not pw:
                st.error("パスワードを入力してください")
            else:
                doctor = verify_doctor_by_account(account.strip(), pw)
                if doctor:
                    reset_rate_limit("doctor")
                    log_event("doctor_login_success", account.strip())
                    st.session_state.doctor_authenticated = True
                    st.session_state.doctor_id = doctor["id"]
                    st.rerun()
                else:
                    record_failed_attempt("doctor")
                    log_event("doctor_login_failed", account.strip())
                    st.error("アカウント名またはパスワードが正しくありません")

        if st.button("パスワードを忘れた方"):
            st.session_state._pw_reset_mode = True
            st.rerun()

    st.markdown("---")
    if st.button("← 戻る"):
        st.session_state.role = None
        st.session_state.pop("_pw_reset_mode", None)
        st.rerun()


def _show_admin_header():
    """管理者用ヘッダー：タイトル・対象月セレクタ・希望入力公開設定・ログアウト"""
    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(4)]

    # デフォルト月: session_stateに明示的な値があればそれを使う。
    # なければ公開月（open_month）をデフォルトにする。
    key = "admin_target_month"
    # スケジュール確定後の次月切替（widget keyは直接設定不可なので間接キー経由）
    pending = st.session_state.pop("_pending_target_month", None)
    if pending and pending in months:
        st.session_state[key] = pending
    elif key not in st.session_state:
        current_open = get_open_month()
        if current_open and current_open in months:
            st.session_state[key] = current_open
    elif st.session_state[key] not in months:
        # 選択肢外の値（過去月など）はリセット
        del st.session_state[key]

    col_title, col_month, col_logout = st.columns([3, 2, 1])
    with col_title:
        st.markdown("**管理者メニュー**")
    with col_month:
        target_month = st.selectbox(
            "対象月", months, key=key, label_visibility="collapsed",
        )
    with col_logout:
        if st.button("ログアウト", use_container_width=True):
            st.session_state.role = None
            st.session_state.admin_authenticated = False
            st.session_state.doctor_authenticated = False
            st.session_state.doctor_id = None
            st.rerun()

    year, month = map(int, target_month.split("-"))
    sat_count = len(get_target_saturdays(year, month))

    # 希望入力の公開設定（対象月 + 入力期限）
    current_open = get_open_month()
    current_deadline = get_input_deadline()
    open_label = f"公開中: {current_open}" if current_open else "未公開"
    deadline_label = f"（期限: {current_deadline}）" if current_deadline else ""

    col_info, col_open, col_deadline = st.columns([3, 2, 2])
    with col_info:
        st.caption(f"対象土曜日数: {sat_count}日　｜　希望入力 {open_label}{deadline_label}")
    with col_open:
        if st.button(
            f"この月を医員に公開",
            key="set_open_month_header",
            use_container_width=True,
            type="primary" if current_open != target_month else "secondary",
        ):
            set_open_month(target_month)
            st.rerun()
    with col_deadline:
        default_deadline = (
            date.fromisoformat(current_deadline)
            if current_deadline
            else today + relativedelta(days=7)
        )
        deadline_date = st.date_input(
            "入力期限", value=default_deadline,
            key="header_deadline",
            label_visibility="collapsed",
            on_change=lambda: set_input_deadline(
                st.session_state["header_deadline"].isoformat()
            ),
        )

    st.markdown("---")
    return target_month, year, month


def _show_doctor_settings(doctor):
    """医員設定: アカウント名変更・パスワード変更・メールアドレス設定"""
    with st.expander("アカウント設定", expanded=True):
        st.caption(f"ID: {doctor.get('account', '')}　|　アカウント名: {doctor.get('account_name', '')}")

        tab_acc, tab_pw, tab_email = st.tabs(["アカウント名変更", "パスワード変更", "メールアドレス設定"])

        with tab_acc:
            with st.form("change_account_name_form"):
                current_aname = doctor.get("account_name", "")
                new_aname = st.text_input("新しいアカウント名", value=current_aname)
                if st.form_submit_button("アカウント名を変更"):
                    if not new_aname.strip():
                        st.error("アカウント名を入力してください")
                    elif new_aname.strip() == current_aname:
                        st.info("変更はありません")
                    else:
                        err = update_doctor_account_name(doctor["id"], new_aname.strip())
                        if err == "duplicate":
                            st.error(f"アカウント名「{new_aname}」は既に使用されています")
                        else:
                            st.success("アカウント名を変更しました")
                            st.rerun()

        with tab_pw:
            with st.form("change_password_form"):
                current_pw = st.text_input("現在のパスワード", type="password")
                new_pw1 = st.text_input("新しいパスワード", type="password")
                new_pw2 = st.text_input("新しいパスワード（確認）", type="password")
                if st.form_submit_button("パスワードを変更"):
                    if not current_pw or not new_pw1:
                        st.error("すべての項目を入力してください")
                    elif not verify_doctor_individual_password(doctor["id"], current_pw):
                        st.error("現在のパスワードが正しくありません")
                    elif new_pw1 != new_pw2:
                        st.error("新しいパスワードが一致しません")
                    else:
                        pw_ok, pw_msg = validate_password(new_pw1)
                        if not pw_ok:
                            st.error(pw_msg)
                        else:
                            set_doctor_individual_password(doctor["id"], new_pw1)
                            log_event("doctor_password_changed", doctor.get("account_name", ""))
                            st.success("パスワードを変更しました")

        with tab_email:
            with st.form("change_email_form"):
                current_email = doctor.get("email", "")
                if current_email:
                    st.write(f"現在のメールアドレス: {current_email}")
                else:
                    st.write("メールアドレスが未設定です")
                new_email = st.text_input("メールアドレス", value=current_email)
                if st.form_submit_button("メールアドレスを保存"):
                    if new_email.strip() and not validate_email(new_email.strip()):
                        st.error("メールアドレスの形式が正しくありません")
                    else:
                        update_doctor_email(doctor["id"], new_email.strip())
                        st.success("メールアドレスを保存しました")
                    st.rerun()

        if st.button("設定を閉じる"):
            st.session_state.pop("show_doctor_settings", None)
            st.rerun()


# ---- メインルーティング ----
if st.session_state.role is None:
    _show_role_selection()

elif st.session_state.role == "admin":
    if not st.session_state.admin_authenticated:
        _show_admin_login()
    else:
        target_month, year, month = _show_admin_header()

        tab1, tab2, tab3, tab4 = st.tabs([
            "マスタ管理", "希望状況一覧",
            "スケジュール生成", "スケジュール確認",
        ])

        with tab1:
            admin_master.render(target_month, year, month)
        with tab2:
            admin_preferences.render(target_month, year, month)
        with tab3:
            admin_generate.render(target_month, year, month)
        with tab4:
            admin_schedule.render(target_month)

elif st.session_state.role == "doctor":
    if not st.session_state.doctor_authenticated:
        _show_doctor_login()
    else:
        doctors = get_doctors(active_only=False)
        doctor = next((d for d in doctors if d["id"] == st.session_state.doctor_id), None)
        if doctor is None or not doctor.get("can_login", 1):
            st.session_state.doctor_authenticated = False
            st.session_state.doctor_id = None
            if doctor and not doctor.get("can_login", 1):
                st.warning("ログインが停止されています。管理者にお問い合わせください。")
                st.stop()
            st.rerun()
        else:
            # 医員用ヘッダー（対象月セレクタなし）
            col_title, col_settings, col_logout = st.columns([4, 1, 1])
            with col_title:
                st.markdown(f"**{doctor['name']}**")
            with col_settings:
                if st.button("⚙ 設定", use_container_width=True):
                    st.session_state.show_doctor_settings = True
            with col_logout:
                if st.button("ログアウト", use_container_width=True):
                    st.session_state.role = None
                    st.session_state.admin_authenticated = False
                    st.session_state.doctor_authenticated = False
                    st.session_state.doctor_id = None
                    st.session_state.pop("show_doctor_settings", None)
                    st.rerun()

            if st.session_state.get("show_doctor_settings"):
                _show_doctor_settings(doctor)

            st.markdown("---")

            tab1, tab2 = st.tabs(["希望入力", "スケジュール確認"])

            with tab1:
                open_month = get_open_month()
                if open_month:
                    year, month = map(int, open_month.split("-"))
                    deadline = get_input_deadline()
                    deadline_text = f"　|　入力期限: {deadline}" if deadline else ""
                    st.caption(f"対象月: {open_month}　|　対象土曜日数: {len(get_target_saturdays(year, month))}日{deadline_text}")
                    doctor_input.render(doctor, open_month, year, month)
                else:
                    st.info("管理者が対象月を設定するまでお待ちください。")

            with tab2:
                confirmed_months = get_confirmed_months()
                if confirmed_months:
                    view_month = st.selectbox(
                        "月を選択", confirmed_months,
                        label_visibility="collapsed",
                    )
                    doctor_schedule.render(doctor, view_month)
                else:
                    st.info("確定済みのスケジュールはまだありません。")
