"""
外勤調整システム - メインアプリケーション
Streamlit ベースの Web アプリ
"""
import streamlit as st
from datetime import date
from dateutil.relativedelta import relativedelta

from database import (
    init_db, get_doctors,
    is_admin_password_set, set_admin_password, verify_admin_password,
    is_doctor_individual_password_set, set_doctor_individual_password,
    verify_doctor_individual_password, verify_doctor_by_account,
    update_doctor_email, update_doctor_account_name,
    get_open_month, get_input_deadline, get_confirmed_months,
)
from optimizer import get_target_saturdays
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
        st.info("管理者パスワードが未設定です。初回パスワードを設定してください。")
        pw1 = st.text_input("パスワード", type="password", key="pw_new1")
        pw2 = st.text_input("パスワード（確認）", type="password", key="pw_new2")
        if st.button("パスワードを設定", type="primary"):
            if not pw1:
                st.error("パスワードを入力してください")
            elif pw1 != pw2:
                st.error("パスワードが一致しません")
            else:
                set_admin_password(pw1)
                st.session_state.admin_authenticated = True
                st.success("パスワードを設定しました")
                st.rerun()
    else:
        pw = st.text_input("パスワード", type="password", key="pw_login")
        if st.button("ログイン", type="primary"):
            if verify_admin_password(pw):
                st.session_state.admin_authenticated = True
                st.rerun()
            else:
                st.error("パスワードが正しくありません")

    st.markdown("---")
    if st.button("← 戻る"):
        st.session_state.role = None
        st.rerun()


def _show_doctor_login():
    """医員ログイン画面（アカウント＋パスワード入力）"""
    st.title("医員ログイン")
    st.markdown("---")

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
                st.session_state.doctor_authenticated = True
                st.session_state.doctor_id = doctor["id"]
                st.rerun()
            else:
                st.error("アカウント名またはパスワードが正しくありません")

    st.markdown("---")
    if st.button("← 戻る"):
        st.session_state.role = None
        st.rerun()


def _show_admin_header():
    """管理者用ヘッダー：タイトル・対象月セレクタ・ログアウト"""
    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(4)]

    col_title, col_month, col_logout = st.columns([3, 2, 1])
    with col_title:
        st.markdown("**管理者メニュー**")
    with col_month:
        target_month = st.selectbox(
            "対象月", months, label_visibility="collapsed",
        )
    with col_logout:
        if st.button("ログアウト", use_container_width=True):
            st.session_state.role = None
            st.session_state.admin_authenticated = False
            st.session_state.doctor_authenticated = False
            st.session_state.doctor_id = None
            st.rerun()

    year, month = map(int, target_month.split("-"))
    st.caption(f"対象土曜日数: {len(get_target_saturdays(year, month))}日")
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
                        set_doctor_individual_password(doctor["id"], new_pw1)
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
        doctors = get_doctors()
        doctor = next((d for d in doctors if d["id"] == st.session_state.doctor_id), None)
        if doctor is None:
            st.session_state.doctor_authenticated = False
            st.session_state.doctor_id = None
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
