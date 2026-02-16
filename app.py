"""
外勤調整システム - メインアプリケーション
Streamlit ベースの Web アプリ
"""
import streamlit as st
from datetime import date
from dateutil.relativedelta import relativedelta

from database import (
    init_db, get_doctors, delete_old_schedules,
    is_admin_password_set, set_admin_password, verify_admin_password,
    is_doctor_password_set, set_doctor_password, verify_doctor_password,
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

init_db()
delete_old_schedules(months_to_keep=4)

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
    """医員パスワード認証画面"""
    st.title("医員ログイン")
    st.markdown("---")

    if not is_doctor_password_set():
        st.info("医員用パスワードが未設定です。管理者画面で設定してください。")
    else:
        pw = st.text_input("パスワード", type="password", key="doc_pw_login")
        if st.button("ログイン", type="primary"):
            if verify_doctor_password(pw):
                st.session_state.doctor_authenticated = True
                st.rerun()
            else:
                st.error("パスワードが正しくありません")

    st.markdown("---")
    if st.button("← 戻る"):
        st.session_state.role = None
        st.rerun()


def _show_doctor_selection():
    """医員選択画面（認証済み後）"""
    st.title("医員ログイン")
    st.markdown("---")

    doctors = get_doctors()
    if not doctors:
        st.warning("医員が登録されていません。管理者にお問い合わせください。")
    else:
        doctor_names = [d["name"] for d in doctors]
        selected = st.selectbox("名前を選択してください", doctor_names)
        if st.button("選択", type="primary"):
            doctor = next(d for d in doctors if d["name"] == selected)
            st.session_state.doctor_id = doctor["id"]
            st.rerun()

    st.markdown("---")
    if st.button("← 戻る"):
        st.session_state.role = None
        st.session_state.doctor_authenticated = False
        st.rerun()


def _show_header(title):
    """ヘッダー：タイトル・対象月セレクタ・ログアウト"""
    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(4)]

    col_title, col_month, col_logout = st.columns([3, 2, 1])
    with col_title:
        st.markdown(f"**{title}**")
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


# ---- メインルーティング ----
if st.session_state.role is None:
    _show_role_selection()

elif st.session_state.role == "admin":
    if not st.session_state.admin_authenticated:
        _show_admin_login()
    else:
        target_month, year, month = _show_header("管理者メニュー")

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
    elif st.session_state.doctor_id is None:
        _show_doctor_selection()
    else:
        doctors = get_doctors()
        doctor = next((d for d in doctors if d["id"] == st.session_state.doctor_id), None)
        if doctor is None:
            _show_doctor_selection()
        else:
            target_month, year, month = _show_header(doctor['name'])

            tab1, tab2 = st.tabs(["希望入力", "スケジュール確認"])

            with tab1:
                doctor_input.render(doctor, target_month, year, month)
            with tab2:
                doctor_schedule.render(doctor, target_month)
