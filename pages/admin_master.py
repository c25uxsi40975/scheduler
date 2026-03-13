"""管理者: マスタ管理タブ"""
import json
import pandas as pd
import streamlit as st
from database import (
    get_doctors, add_doctor, update_doctor, delete_doctor,
    get_clinics, add_clinic, update_clinic, delete_clinic,
    get_affinities, set_affinity, batch_set_affinities,
    batch_update_max_assignments,
    get_clinic_date_overrides, set_clinic_date_overrides_batch,
    set_doctor_individual_password, update_doctor_email,
    get_all_preferences, upsert_preference, batch_upsert_preferences,
    # 平日外勤
    get_weekday_configs, add_weekday_config, update_weekday_config, delete_weekday_config,
    get_saturday_extra_dates, set_saturday_extra_dates,
    get_saturday_excluded_dates, set_saturday_excluded_dates,
)
from optimizer import get_target_saturdays, get_clinic_dates
from components.display_utils import build_display_name_map
# 優先度ラベル定義（weight値とラベルの対応）
WEIGHT_TO_LABEL = {3.0: "必須", 2.0: "指名", 1.0: "任意", 0.0: "除外"}
LABEL_TO_WEIGHT = {"必須": 3.0, "指名": 2.0, "任意": 1.0, "除外": 0.0}
PRIORITY_LABELS = ["必須", "指名", "任意", "除外"]


HOURS = list(range(25))  # 0〜24
MINUTES_OPTIONS = [0, 30]


def _time_select(label: str, default: str, key_prefix: str):
    """X時Y分のプルダウンで時間を入力し "HH:MM" を返す"""
    h_default, m_default = 9, 0
    if default:
        try:
            parts = default.split(":")
            h_default = int(parts[0])
            m_default = int(parts[1]) if len(parts) > 1 else 0
            if m_default not in MINUTES_OPTIONS:
                m_default = 0
        except (ValueError, IndexError):
            pass
    c1, c2 = st.columns(2)
    with c1:
        h = st.selectbox(f"{label}（時）", HOURS, index=h_default,
                         key=f"{key_prefix}_h", label_visibility="collapsed")
    with c2:
        m = st.selectbox(f"{label}（分）", MINUTES_OPTIONS,
                         index=MINUTES_OPTIONS.index(m_default),
                         key=f"{key_prefix}_m", label_visibility="collapsed")
    return f"{h:02d}:{m:02d}"


FREQ_OPTIONS = [
    ("weekly", "毎週"),
    ("biweekly_odd", "隔週（奇数週）"),
    ("biweekly_even", "隔週（偶数週）"),
    ("first_only", "第1週のみ"),
    ("last_only", "最終週のみ"),
    ("irregular", "不定期"),
]
FREQ_LABELS = {k: v for k, v in FREQ_OPTIONS}

# 外勤先テンプレート（Excel③出張先マスタの定義値）
CLINIC_TEMPLATES = {
    "鴨川病院":   {"fee": 75000,  "effort_cost": 1,  "work_hours": 2.5, "time_slot": "AM",  "location": "鴨川市"},
    "あすみが丘": {"fee": 60000,  "effort_cost": 2,  "work_hours": 3.0, "time_slot": "AM",  "location": "千葉市"},
    "習志野第一": {"fee": 50000,  "effort_cost": 3,  "work_hours": 3.5, "time_slot": "AM",  "location": "習志野市"},
    "有本":       {"fee": 60000,  "effort_cost": 4,  "work_hours": 3.0, "time_slot": "AM",  "location": "市川市"},
    "土井":       {"fee": 70000,  "effort_cost": 5,  "work_hours": 3.5, "time_slot": "AM",  "location": "船橋市"},
    "沼南":       {"fee": 100000, "effort_cost": 6,  "work_hours": 5.0, "time_slot": "ALL", "location": "柏市"},
    "和田":       {"fee": 80000,  "effort_cost": 7,  "work_hours": 5.0, "time_slot": "PM",  "location": "市原市"},
    "双葉":       {"fee": 100000, "effort_cost": 8,  "work_hours": 5.0, "time_slot": "ALL", "location": "千葉市"},
    "千葉駅":     {"fee": 100000, "effort_cost": 9,  "work_hours": 6.0, "time_slot": "ALL", "location": "千葉市"},
    "稲毛":       {"fee": 120000, "effort_cost": 10, "work_hours": 7.0, "time_slot": "ALL", "location": "千葉市"},
}


def render(target_month, year, month):
    if not st.session_state.get("admin_authenticated"):
        st.stop()
    st.header("マスタ管理")

    # 行レベルの背景色CSS + スマホ向けコンパクト化
    st.markdown("""<style>
    [data-testid="stVerticalBlockBorderWrapper"]:has(.row-active) {
        background-color: #e8f5e9 !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:has(.row-inactive) {
        background-color: #ffebee !important;
    }
    .row-active, .row-inactive { display: none; }

    /* スマホ向けコンパクト化 */
    @media (max-width: 768px) {
        .stMainBlockContainer { padding: 0.5rem !important; }
        h2 { font-size: 1.2rem !important; }
        h3 { font-size: 1rem !important; }
        p, .stMarkdown, .stText { font-size: 0.85rem !important; }
        .stButton > button {
            font-size: 0.75rem !important;
            padding: 0.2rem 0.5rem !important;
            min-height: 1.8rem !important;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            padding: 0.3rem !important;
        }
        [data-testid="stFormSubmitButton"] > button {
            font-size: 0.8rem !important;
        }
        .stRadio label { font-size: 0.8rem !important; }
        .stSelectbox label, .stTextInput label { font-size: 0.8rem !important; }
    }
    </style>""", unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    # ---- 医員管理 ----
    with col1:
        st.subheader("医員一覧")
        with st.expander("医員の追加・編集", expanded=False):
            with st.form("add_doctor_form", clear_on_submit=True):
                name_cols = st.columns(2)
                with name_cols[0]:
                    new_last = st.text_input("名字")
                with name_cols[1]:
                    new_first = st.text_input("名前")
                new_account = st.text_input("医員ID（入局年度）", placeholder="例: 2024")
                new_init_pw = st.text_input("初期パスワード", value="aaaa1111")
                st.caption("初期アカウント名 = 医員ID。アカウント名はユーザーが後から変更可能です。")
                if st.form_submit_button("追加", use_container_width=True):
                    if not new_last.strip():
                        st.error("名字を入力してください")
                    elif not new_first.strip():
                        st.error("名前を入力してください")
                    elif not new_account.strip():
                        st.error("医員IDを入力してください")
                    elif not new_init_pw.strip():
                        st.error("初期パスワードを入力してください")
                    else:
                        err = add_doctor(new_last.strip(), new_first.strip(), account=new_account.strip(), initial_password=new_init_pw.strip())
                        if err == "duplicate_account":
                            st.error(f"医員ID「{new_account}」は既に使用されています")
                        else:
                            st.session_state["_toast_msg"] = f"「{new_last}{new_first}」を追加しました"
                            st.rerun()

            doctors_all = get_doctors(active_only=False)
            if doctors_all:
                def _doc_label(d):
                    s = "有効" if d["is_active"] else "無効"
                    login = "" if d.get("can_login", 1) else " [ログイン停止]"
                    pw = "🔑" if d.get("password_hash") else "⚠️"
                    acc = d.get("account", "")
                    acc_str = f" [ID:{acc}]" if acc else ""
                    return f"{d['name']}{acc_str}（{s}）{login}{pw}"

                selected_doc = st.selectbox(
                    "医員を選択", doctors_all,
                    format_func=_doc_label, key="select_doctor"
                )

                if selected_doc:
                    d = selected_doc
                    has_pw = bool(d.get("password_hash"))
                    has_email = bool(d.get("email"))
                    marker = "row-active" if d['is_active'] else "row-inactive"
                    status_label = "有効" if d['is_active'] else "無効"
                    login_label = "ログイン可" if d.get('can_login', 1) else "ログイン停止"
                    id_display = d.get("account", "") or "未設定"
                    aname_display = d.get("account_name", "") or id_display
                    email_display = d.get("email", "") or "未設定"
                    max_a = d.get("max_assignments", 0)
                    limit_display = f"{max_a}回/月" if max_a > 0 else "未設定"
                    rank_labels = {0: "未設定", 1: "レジデント", 2: "大学院生", 3: "フェロー"}
                    rank_display = rank_labels.get(d.get("job_rank", 0), "未設定")
                    with st.container(border=True):
                        st.markdown(f'<span class="{marker}"></span>', unsafe_allow_html=True)
                        st.markdown(f"**{d['name']}**　{status_label}　{login_label}　ID: {id_display}　アカウント名: {aname_display}　📧 {email_display}　上限: {limit_display}　役職: {rank_display}")
                        b1, b1b, b2, b3, b4, b5 = st.columns(6)
                        with b1:
                            if d['is_active']:
                                if st.button("シフト除外", key=f"deact_{d['id']}", type="secondary", use_container_width=True):
                                    update_doctor(d['id'], is_active=0)
                                    st.rerun()
                            else:
                                if st.button("シフト有効", key=f"act_{d['id']}", use_container_width=True):
                                    update_doctor(d['id'], is_active=1)
                                    st.rerun()
                        with b1b:
                            if d.get('can_login', 1):
                                if st.button("ログイン停止", key=f"login_off_{d['id']}", type="secondary", use_container_width=True):
                                    update_doctor(d['id'], can_login=0)
                                    st.rerun()
                            else:
                                if st.button("ログイン許可", key=f"login_on_{d['id']}", use_container_width=True):
                                    update_doctor(d['id'], can_login=1)
                                    st.rerun()
                        with b2:
                            btn_label = "PW再設定" if has_pw else "PW設定"
                            if st.button(btn_label, key=f"setpw_{d['id']}", use_container_width=True):
                                st.session_state[f"setting_pw_{d['id']}"] = True
                        with b3:
                            email_btn = "📧変更" if has_email else "📧設定"
                            if st.button(email_btn, key=f"setemail_{d['id']}", use_container_width=True):
                                st.session_state[f"setting_email_{d['id']}"] = True
                        with b4:
                            if st.button("役職", key=f"setrank_{d['id']}", use_container_width=True):
                                st.session_state[f"setting_rank_{d['id']}"] = True
                        with b5:
                            if st.button("削除", key=f"del_doc_{d['id']}", type="secondary", use_container_width=True):
                                st.session_state[f"confirm_del_doc_{d['id']}"] = True

                    # パスワード設定フォーム
                    if st.session_state.get(f"setting_pw_{d['id']}"):
                        with st.form(f"setpw_form_{d['id']}"):
                            pw1 = st.text_input("パスワード", type="password", key=f"pw1_{d['id']}")
                            pw2 = st.text_input("パスワード（確認）", type="password", key=f"pw2_{d['id']}")
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("設定"):
                                    if not pw1:
                                        st.error("パスワードを入力してください")
                                    elif pw1 != pw2:
                                        st.error("パスワードが一致しません")
                                    else:
                                        set_doctor_individual_password(d['id'], pw1)
                                        st.session_state["_toast_msg"] = f"「{d['name']}」のパスワードを設定しました"
                                        st.session_state.pop(f"setting_pw_{d['id']}", None)
                                        st.rerun()
                            with fc2:
                                if st.form_submit_button("キャンセル"):
                                    st.session_state.pop(f"setting_pw_{d['id']}", None)
                                    st.rerun()

                    # メールアドレス設定フォーム
                    if st.session_state.get(f"setting_email_{d['id']}"):
                        with st.form(f"setemail_form_{d['id']}"):
                            current_email = d.get("email", "") or ""
                            new_email = st.text_input("メールアドレス", value=current_email, key=f"email_{d['id']}")
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("保存"):
                                    update_doctor_email(d['id'], new_email.strip())
                                    st.session_state["_toast_msg"] = f"「{d['name']}」のメールアドレスを保存しました"
                                    st.session_state.pop(f"setting_email_{d['id']}", None)
                                    st.rerun()
                            with fc2:
                                if st.form_submit_button("キャンセル"):
                                    st.session_state.pop(f"setting_email_{d['id']}", None)
                                    st.rerun()

                    # 削除確認
                    if st.session_state.get(f"confirm_del_doc_{d['id']}"):
                        st.warning(f"「{d['name']}」を削除しますか？関連データも削除されます。")
                        dc1, dc2 = st.columns(2)
                        with dc1:
                            if st.button("削除する", key=f"do_del_doc_{d['id']}", type="primary"):
                                delete_doctor(d['id'])
                                st.session_state.pop(f"confirm_del_doc_{d['id']}", None)
                                st.session_state["_toast_msg"] = "削除しました"
                                st.rerun()
                        with dc2:
                            if st.button("キャンセル", key=f"cancel_del_doc_{d['id']}"):
                                st.session_state.pop(f"confirm_del_doc_{d['id']}", None)
                                st.rerun()

                    # 役職ランク設定フォーム
                    if st.session_state.get(f"setting_rank_{d['id']}"):
                        with st.form(f"setrank_form_{d['id']}"):
                            rank_options = [
                                (0, "未設定"), (1, "レジデント"),
                                (2, "大学院生"), (3, "フェロー"),
                            ]
                            current_rank = d.get("job_rank", 0)
                            new_rank = st.selectbox(
                                "役職ランク",
                                rank_options,
                                index=current_rank,
                                format_func=lambda x: x[1],
                                key=f"rank_val_{d['id']}",
                            )
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("保存"):
                                    update_doctor(d['id'], job_rank=new_rank[0])
                                    st.session_state["_toast_msg"] = f"役職を{new_rank[1]}に設定しました"
                                    st.session_state.pop(f"setting_rank_{d['id']}", None)
                                    st.rerun()
                            with fc2:
                                if st.form_submit_button("キャンセル"):
                                    st.session_state.pop(f"setting_rank_{d['id']}", None)
                                    st.rerun()

    # ---- 外勤先管理 ----
    with col2:
        st.subheader("外勤先一覧")
        with st.expander("外勤先の追加・編集", expanded=False):
            # テンプレート選択（フォーム外で選択→session_stateで値を渡す）
            template_keys = ["（手動入力）"] + list(CLINIC_TEMPLATES.keys())
            selected_tpl = st.selectbox(
                "テンプレートから選択", template_keys,
                key="clinic_template_select",
                help="既知の外勤先を選ぶと日当・労力コスト等が自動入力されます",
            )
            tpl = CLINIC_TEMPLATES.get(selected_tpl, {})

            _add_form_doctors = get_doctors()
            _add_doc_id_name = build_display_name_map(_add_form_doctors)
            _add_doc_ids = [d["id"] for d in _add_form_doctors]

            with st.form("add_clinic_form", clear_on_submit=True):
                new_clinic = st.text_input("外勤先名", value=selected_tpl if tpl else "")
                new_fee = st.number_input("日当（円）", min_value=0, step=10000,
                                          value=tpl.get("fee", 50000))
                new_freq = st.selectbox("頻度", FREQ_OPTIONS, format_func=lambda x: x[1])
                new_effort = st.number_input("労力コスト (1-10)", min_value=0, max_value=10,
                                             step=1, value=tpl.get("effort_cost", 0))
                new_hours = st.number_input("勤務時間 (h)", min_value=0.0, max_value=12.0,
                                            step=0.5, value=float(tpl.get("work_hours", 0)))
                tslot_options = ["", "AM", "PM", "ALL"]
                tpl_tslot = tpl.get("time_slot", "")
                new_tslot = st.selectbox("時間帯", tslot_options,
                                         index=tslot_options.index(tpl_tslot) if tpl_tslot in tslot_options else 0,
                                         help="AM=午前のみ / PM=午後のみ / ALL=終日。当直明け○の医員はPMの外勤先のみ割当可能です")
                new_loc = st.text_input("勤務地", value=tpl.get("location", ""))
                st.caption("開始時間（カレンダー表示用）")
                new_start_time = _time_select("開始", tpl.get("start_time", ""), "add_cli_start")
                st.caption("終了時間（カレンダー表示用）")
                new_end_time = _time_select("終了", tpl.get("end_time", ""), "add_cli_end")
                new_limited = st.multiselect(
                    "限定メンバー", options=_add_doc_ids,
                    format_func=lambda x: _add_doc_id_name.get(x, "?"),
                    help="設定すると、この外勤先にはリスト内の医員のみ割り当て可能になります（ホワイトリスト）",
                )
                if st.form_submit_button("追加", use_container_width=True):
                    if new_clinic.strip():
                        add_clinic(
                            new_clinic.strip(), new_fee, new_freq[0],
                            effort_cost=new_effort, work_hours=new_hours,
                            time_slot=new_tslot, location=new_loc,
                            start_time=new_start_time, end_time=new_end_time,
                            fixed_doctors=new_limited,
                        )
                        st.success(f"「{new_clinic}」を追加しました")
                        st.rerun()

            clinics_all = get_clinics(active_only=False)
            if clinics_all:
                def _cli_label(c):
                    s = "有効" if c["is_active"] else "無効"
                    return f"{c['name']}（{s}）"

                selected_cli = st.selectbox(
                    "外勤先を選択", clinics_all,
                    format_func=_cli_label, key="select_clinic"
                )

                if selected_cli:
                    c = selected_cli
                    marker = "row-active" if c['is_active'] else "row-inactive"
                    status_label = "有効" if c['is_active'] else "無効"
                    effort = c.get("effort_cost", 0)
                    hours = c.get("work_hours", 0)
                    tslot = c.get("time_slot", "")
                    loc = c.get("location", "")
                    _edit_docs = get_doctors()
                    _edit_doc_id_name = build_display_name_map(_edit_docs)
                    _edit_doc_ids = [d["id"] for d in _edit_docs]

                    with st.container(border=True):
                        st.markdown(f'<span class="{marker}"></span>', unsafe_allow_html=True)
                        info_parts = [
                            f"**{c['name']}**　{status_label}",
                            f"¥{c['fee']:,}",
                            FREQ_LABELS.get(c['frequency'], c['frequency']),
                        ]
                        if effort:
                            info_parts.append(f"労力:{effort:.0f}")
                        if hours:
                            info_parts.append(f"{hours:.1f}h")
                        if tslot:
                            info_parts.append(tslot)
                        cli_start = c.get("start_time", "")
                        cli_end = c.get("end_time", "")
                        if cli_start and cli_end:
                            info_parts.append(f"{cli_start}〜{cli_end}")
                        if loc:
                            info_parts.append(loc)
                        fd_list = c.get("fixed_doctors") or []
                        if fd_list:
                            fd_names = ", ".join(_edit_doc_id_name.get(did, "?") for did in fd_list)
                            info_parts.append(f"限定:[{fd_names}]")
                        st.markdown(" | ".join(info_parts))
                        bc1, bc2 = st.columns(2)
                        with bc1:
                            if c['is_active']:
                                if st.button("無効化", key=f"deact_cli_{c['id']}", type="secondary", use_container_width=True):
                                    update_clinic(c['id'], is_active=0)
                                    st.rerun()
                            else:
                                if st.button("有効化", key=f"act_cli_{c['id']}", use_container_width=True):
                                    update_clinic(c['id'], is_active=1)
                                    st.rerun()
                        with bc2:
                            if st.button("編集", key=f"edit_cli_{c['id']}", use_container_width=True):
                                st.session_state[f"editing_cli_{c['id']}"] = True

                    # 外勤先編集フォーム
                    if st.session_state.get(f"editing_cli_{c['id']}"):
                        with st.form(f"edit_clinic_form_{c['id']}"):
                            edit_fee = st.number_input(
                                "日当（円）", min_value=0, step=10000,
                                value=c["fee"], key=f"fee_{c['id']}"
                            )
                            current_freq_idx = next(
                                (i for i, (k, _) in enumerate(FREQ_OPTIONS) if k == c["frequency"]),
                                0
                            )
                            edit_freq = st.selectbox(
                                "頻度", FREQ_OPTIONS,
                                index=current_freq_idx,
                                format_func=lambda x: x[1],
                                key=f"freq_{c['id']}"
                            )
                            edit_effort = st.number_input(
                                "労力コスト (1-10)", min_value=0, max_value=10, step=1,
                                value=int(c.get("effort_cost", 0)),
                                key=f"effort_{c['id']}"
                            )
                            edit_hours = st.number_input(
                                "勤務時間 (h)", min_value=0.0, max_value=12.0, step=0.5,
                                value=float(c.get("work_hours", 0)),
                                key=f"hours_{c['id']}"
                            )
                            time_slot_options = ["", "AM", "PM", "ALL"]
                            current_tslot = c.get("time_slot", "")
                            tslot_idx = time_slot_options.index(current_tslot) if current_tslot in time_slot_options else 0
                            edit_tslot = st.selectbox(
                                "時間帯", time_slot_options,
                                index=tslot_idx,
                                key=f"tslot_{c['id']}",
                                help="AM=午前のみ / PM=午後のみ / ALL=終日。当直明け○の医員はPMの外勤先のみ割当可能です",
                            )
                            edit_loc = st.text_input(
                                "勤務地", value=c.get("location", ""),
                                key=f"loc_{c['id']}"
                            )
                            st.caption("開始時間（カレンダー表示用）")
                            edit_start_time = _time_select(
                                "開始", c.get("start_time", ""),
                                f"cli_start_{c['id']}")
                            st.caption("終了時間（カレンダー表示用）")
                            edit_end_time = _time_select(
                                "終了", c.get("end_time", ""),
                                f"cli_end_{c['id']}")
                            current_fd = c.get("fixed_doctors") or []
                            # default にはリスト内のIDのうち、現在有効な医員のみ
                            edit_limited = st.multiselect(
                                "限定メンバー", options=_edit_doc_ids,
                                default=[did for did in current_fd if did in _edit_doc_id_name],
                                format_func=lambda x: _edit_doc_id_name.get(x, "?"),
                                key=f"limited_{c['id']}",
                                help="設定すると、この外勤先にはリスト内の医員のみ割り当て可能になります（ホワイトリスト）",
                            )
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("保存"):
                                    update_clinic(
                                        c['id'], fee=edit_fee, frequency=edit_freq[0],
                                        effort_cost=edit_effort, work_hours=edit_hours,
                                        time_slot=edit_tslot, location=edit_loc,
                                        start_time=edit_start_time, end_time=edit_end_time,
                                        fixed_doctors=edit_limited,
                                    )
                                    st.session_state.pop(f"editing_cli_{c['id']}", None)
                                    st.session_state["_toast_msg"] = "保存しました"
                                    st.rerun()
                            with fc2:
                                if st.form_submit_button("キャンセル"):
                                    st.session_state.pop(f"editing_cli_{c['id']}", None)
                                    st.rerun()

    clinics = get_clinics()
    doctors = get_doctors()

    # 保存成功メッセージ（前回の保存結果を表示）
    if st.session_state.get("_save_msg"):
        st.toast(st.session_state.pop("_save_msg"))

    # ---- セクション2: 外勤先の指名・優先度設定 ----
    st.markdown("---")
    st.subheader("外勤先の指名・優先度設定")

    if clinics and doctors:
        all_affinities = get_affinities()

        # 医員のソート: job_rank降順 → 名前順（上級医が上）
        rank_labels = {0: "未設定", 1: "レジデント", 2: "大学院生", 3: "フェロー"}
        sorted_doctors = sorted(doctors, key=lambda d: (-d.get("job_rank", 0), d["name"]))
        _display_map = build_display_name_map(doctors)

        # --- 2A: 優先度マトリクス（編集可能）---
        st.caption(
            "必須: 月1回以上必ず割り当て（ハード制約）／ "
            "指名: できれば来てほしい（ソフト制約）／ "
            "任意: デフォルト ／ "
            "除外: 割り当てない（ハード制約）"
        )

        # 現在のaffinityを (doctor_id, clinic_id) → weight のマップに変換
        aff_map = {}
        for a in all_affinities:
            aff_map[(a["doctor_id"], a["clinic_id"])] = a["weight"]

        # DataFrameを構築（行=医員, 列=外勤先, 値=ラベル）
        matrix_data = {}
        for d in sorted_doctors:
            row_label = f"{_display_map.get(d['id'], d['name'])}({rank_labels.get(d.get('job_rank', 0), '未設定')})"
            row = {}
            for c in clinics:
                w = aff_map.get((d["id"], c["id"]), 1.0)
                row[c["name"]] = WEIGHT_TO_LABEL.get(w, "任意")
            matrix_data[row_label] = row

        df_matrix = pd.DataFrame.from_dict(matrix_data, orient="index")

        # st.data_editor で編集可能なマトリクスを表示
        column_config = {
            c["name"]: st.column_config.SelectboxColumn(
                c["name"], options=PRIORITY_LABELS, default="任意", width="small",
            )
            for c in clinics
        }
        edited_df = st.data_editor(
            df_matrix,
            column_config=column_config,
            use_container_width=True,
            key="priority_matrix",
        )

        if st.button("優先度を一括保存", type="primary", key="save_matrix"):
            updates = []
            for i, d in enumerate(sorted_doctors):
                row_label = f"{_display_map.get(d['id'], d['name'])}({rank_labels.get(d.get('job_rank', 0), '未設定')})"
                for c in clinics:
                    new_label = edited_df.at[row_label, c["name"]]
                    new_w = LABEL_TO_WEIGHT.get(new_label, 1.0)
                    old_w = aff_map.get((d["id"], c["id"]), 1.0)
                    if new_w != old_w:
                        updates.append({"doctor_id": d["id"], "clinic_id": c["id"], "weight": new_w})
            if updates:
                batch_set_affinities(updates)
                st.session_state["_save_msg"] = f"優先度を保存しました（{len(updates)}件変更）"
            else:
                st.session_state["_save_msg"] = "変更はありませんでした"
            st.rerun()

        # --- 2B: 確認ビュー ---
        # 必須/指名/除外/限定がある外勤先のみ表示
        has_special = False
        for c in clinics:
            mandatory_docs = [d for d in sorted_doctors if edited_df.at[
                f"{_display_map.get(d['id'], d['name'])}({rank_labels.get(d.get('job_rank', 0), '未設定')})", c["name"]
            ] == "必須"]
            nominated_docs = [d for d in sorted_doctors if edited_df.at[
                f"{_display_map.get(d['id'], d['name'])}({rank_labels.get(d.get('job_rank', 0), '未設定')})", c["name"]
            ] == "指名"]
            excluded_docs = [d for d in sorted_doctors if edited_df.at[
                f"{_display_map.get(d['id'], d['name'])}({rank_labels.get(d.get('job_rank', 0), '未設定')})", c["name"]
            ] == "除外"]

            # 限定メンバー（外勤先マスタの fixed_doctors）
            fd = c.get("fixed_doctors") or []
            if mandatory_docs or nominated_docs or excluded_docs or fd:
                if not has_special:
                    st.markdown("---")
                    st.write("**設定確認**")
                    has_special = True
                parts = [f"**{c['name']}**: "]
                if fd:
                    fd_names = ", ".join(_display_map.get(did, "?") for did in fd)
                    parts.append(f"限定=[{fd_names}]")
                if mandatory_docs:
                    names = ", ".join(_display_map.get(d["id"], d["name"]) for d in mandatory_docs)
                    parts.append(f"必須=[{names}]")
                if nominated_docs:
                    names = ", ".join(_display_map.get(d["id"], d["name"]) for d in nominated_docs)
                    parts.append(f"指名=[{names}]")
                if excluded_docs:
                    names = ", ".join(_display_map.get(d["id"], d["name"]) for d in excluded_docs)
                    parts.append(f"除外=[{names}]")
                st.caption(" / ".join(parts))

    # ---- セクション3: 医員の希望設定 ----
    st.markdown("---")
    st.subheader("医員の希望設定")

    # --- 3A: 月回数上限の一括設定 ---
    if doctors:
        st.write(f"**月回数上限の一括設定**")
        st.caption("各医員の月あたりの最大外勤回数を設定します（1〜4回）")

        _dmap_3a = build_display_name_map(doctors)
        with st.form("batch_max_assignments"):
            max_cols = st.columns(min(len(doctors), 4))
            for i, d in enumerate(sorted(doctors, key=lambda d: (-d.get("job_rank", 0), d["name"]))):
                rank_labels_3a = {0: "未設定", 1: "レジ", 2: "院生", 3: "フェロー"}
                with max_cols[i % len(max_cols)]:
                    current_max = d.get("max_assignments", 0)
                    if current_max < 1 or current_max > 4:
                        current_max = 4
                    st.number_input(
                        f"{_dmap_3a.get(d['id'], d['name'])}({rank_labels_3a.get(d.get('job_rank', 0), '')})",
                        min_value=1, max_value=4, value=current_max,
                        key=f"max_assign_{d['id']}",
                    )
            if st.form_submit_button("回数上限を一括保存", type="primary"):
                updates = {}
                for d in doctors:
                    new_val = st.session_state.get(f"max_assign_{d['id']}", d.get("max_assignments", 0))
                    current = d.get("max_assignments", 0)
                    if current < 1 or current > 4:
                        current = 4
                    if new_val != current:
                        updates[d["id"]] = new_val
                if updates:
                    batch_update_max_assignments(updates)
                    st.session_state["_save_msg"] = f"回数上限を保存しました（{len(updates)}件変更）"
                else:
                    st.session_state["_save_msg"] = "変更はありませんでした"
                st.rerun()

    # --- 3B-1: 日程マトリクス（医員×日付 ○/△/×） ---
    st.markdown("---")
    st.write(f"**医員の日程希望 — 代理入力 ({target_month})**")
    st.caption("管理者が医員の日程希望をまとめて入力できます（○=可能 当○=当直明け(PMのみ) △=できれば避けたい ×=NG）")

    if doctors:
        saturdays = get_target_saturdays(year, month)
        if not saturdays:
            st.info("対象月に土曜日がありません")
        else:
            prefs_3b = get_all_preferences(target_month)
            pref_map_3b = {p["doctor_id"]: p for p in prefs_3b}

            SCHEDULE_STATUS = ["○", "当○", "△", "×"]
            rank_labels_3b = {0: "未設定", 1: "レジ", 2: "院生", 3: "フェロー"}
            sorted_docs_3b = sorted(doctors, key=lambda d: (-d.get("job_rank", 0), d["name"]))
            _dmap_3b = build_display_name_map(doctors)

            # DataFrame 構築
            matrix_data = {}
            for d in sorted_docs_3b:
                row_label = f"{_dmap_3b.get(d['id'], d['name'])}({rank_labels_3b.get(d.get('job_rank', 0), '')})"
                pref = pref_map_3b.get(d["id"])
                ng_set = set(pref.get("ng_dates", [])) if pref else set()
                avoid_set = set(pref.get("avoid_dates", [])) if pref else set()
                pn_set = set(pref.get("post_night_dates", [])) if pref else set()
                row = {}
                for s in saturdays:
                    ds = s.isoformat()
                    col_label = s.strftime("%m/%d(%a)")
                    if ds in ng_set:
                        row[col_label] = "×"
                    elif ds in avoid_set:
                        row[col_label] = "△"
                    elif ds in pn_set:
                        row[col_label] = "当○"
                    else:
                        row[col_label] = "○"
                matrix_data[row_label] = row

            df_schedule = pd.DataFrame.from_dict(matrix_data, orient="index")
            schedule_col_config = {
                col: st.column_config.SelectboxColumn(
                    col, options=SCHEDULE_STATUS, default="○", width="small",
                )
                for col in df_schedule.columns
            }
            edited_schedule_df = st.data_editor(
                df_schedule,
                column_config=schedule_col_config,
                use_container_width=True,
                key="schedule_matrix",
            )

            if st.button("日程を一括保存", type="primary", key="save_schedule_matrix"):
                batch_items = []
                for d in sorted_docs_3b:
                    row_label = f"{_dmap_3b.get(d['id'], d['name'])}({rank_labels_3b.get(d.get('job_rank', 0), '')})"
                    pref = pref_map_3b.get(d["id"])
                    old_ng = set(pref.get("ng_dates", [])) if pref else set()
                    old_avoid = set(pref.get("avoid_dates", [])) if pref else set()
                    old_pn = set(pref.get("post_night_dates", [])) if pref else set()

                    new_ng = []
                    new_avoid = []
                    new_pn = []
                    for s in saturdays:
                        ds = s.isoformat()
                        col_label = s.strftime("%m/%d(%a)")
                        val = edited_schedule_df.at[row_label, col_label]
                        if val == "×":
                            new_ng.append(ds)
                        elif val == "△":
                            new_avoid.append(ds)
                        elif val == "当○":
                            new_pn.append(ds)

                    if not pref or set(new_ng) != old_ng or set(new_avoid) != old_avoid or set(new_pn) != old_pn:
                        batch_items.append({
                            "doctor_id": d["id"],
                            "ng_dates": new_ng,
                            "avoid_dates": new_avoid,
                            "post_night_dates": new_pn,
                            "preferred_clinics": pref.get("preferred_clinics", []) if pref else [],
                            "date_clinic_requests": pref.get("date_clinic_requests", {}) if pref else {},
                            "free_text": pref.get("free_text", "") if pref else "",
                        })
                if batch_items:
                    batch_upsert_preferences(target_month, batch_items)
                    st.session_state["_save_msg"] = f"日程希望を保存しました（{len(batch_items)}名変更）"
                else:
                    st.session_state["_save_msg"] = "変更はありませんでした"
                st.rerun()

    # --- 3B-2: 個別詳細入力（外勤先希望・備考） ---
    st.markdown("---")
    st.write(f"**個別の外勤先希望・備考 ({target_month})**")
    st.caption("医員ごとに「この日にこの外勤先に行きたい」希望と備考を設定できます")

    if clinics and doctors:
        selected_doctor_dcr = st.selectbox(
            "医員を選択",
            doctors,
            format_func=lambda doc: doc["name"],
            key="dcr_doctor"
        )

        if selected_doctor_dcr:
            saturdays = get_target_saturdays(year, month)
            if not saturdays:
                st.info("対象月に土曜日がありません")
            else:
                prefs_3b2 = get_all_preferences(target_month)
                pref = next((p for p in prefs_3b2 if p["doctor_id"] == selected_doctor_dcr["id"]), None)

                existing_ng = set(pref.get("ng_dates", [])) if pref else set()
                existing_avoid = set(pref.get("avoid_dates", [])) if pref else set()
                existing_dcr = pref.get("date_clinic_requests", {}) if pref else {}
                existing_free_text = pref.get("free_text", "") if pref else ""

                clinic_options = [0] + [cli["id"] for cli in clinics]

                def _dcr_clinic_label(cid):
                    if cid == 0:
                        return "指定なし"
                    return next((cli["name"] for cli in clinics if cli["id"] == cid), str(cid))

                with st.form(f"dcr_form_{selected_doctor_dcr['id']}"):
                    n_cols = min(len(saturdays), 5)
                    dcr_cols = st.columns(n_cols)
                    for i, s in enumerate(saturdays):
                        ds = s.isoformat()
                        with dcr_cols[i % n_cols]:
                            if ds in existing_ng:
                                st.caption(s.strftime("%m/%d") + " ×NG")
                                continue
                            status = "△" if ds in existing_avoid else "○"
                            existing_cid = existing_dcr.get(ds, 0)
                            if isinstance(existing_cid, str):
                                existing_cid = int(existing_cid) if existing_cid.isdigit() else 0
                            default_idx = clinic_options.index(existing_cid) if existing_cid in clinic_options else 0
                            st.selectbox(
                                s.strftime(f"%m/%d({status})"),
                                clinic_options,
                                index=default_idx,
                                format_func=_dcr_clinic_label,
                                key=f"adm_dcr_{selected_doctor_dcr['id']}_{ds}",
                            )

                    st.text_area(
                        "備考",
                        value=existing_free_text,
                        placeholder="例: 学会のため第2週は避けたい",
                        key=f"adm_freetext_{selected_doctor_dcr['id']}",
                    )

                    if st.form_submit_button("外勤先希望・備考を保存", type="primary"):
                        new_dcr = {}
                        for s in saturdays:
                            ds = s.isoformat()
                            if ds in existing_ng:
                                continue
                            val = st.session_state.get(f"adm_dcr_{selected_doctor_dcr['id']}_{ds}", 0)
                            if val != 0:
                                new_dcr[ds] = val
                        new_free_text = st.session_state.get(f"adm_freetext_{selected_doctor_dcr['id']}", "")
                        upsert_preference(
                            selected_doctor_dcr["id"], target_month,
                            ng_dates=list(existing_ng),
                            avoid_dates=list(existing_avoid),
                            preferred_clinics=pref.get("preferred_clinics", []) if pref else [],
                            date_clinic_requests=new_dcr,
                            free_text=new_free_text,
                        )
                        st.session_state["_save_msg"] = f"「{selected_doctor_dcr['name']}」の外勤先希望・備考を保存しました"
                        st.rerun()

    # --- 3C: 外勤先の日別設定 ---
    st.markdown("---")
    st.write(f"**外勤先の日別設定 ({target_month})**")
    st.caption("特定の日に2人体制にする、または休診に設定できます")

    if clinics:
        override_clinic = st.selectbox(
            "外勤先を選択",
            clinics,
            format_func=lambda c: c["name"],
            key="override_clinic"
        )

        if override_clinic:
            saturdays = get_target_saturdays(year, month)
            overrides = get_clinic_date_overrides(target_month)
            is_irregular = override_clinic.get("frequency") == "irregular"

            if is_irregular:
                clinic_sats = saturdays  # 不定期: 全土曜日を表示
                default_req = 0          # デフォルトは休診
                st.caption("不定期の外勤先です。外勤を実施する日を「通常(1人)」または「2人体制」に設定してください")
            else:
                clinic_sats = get_clinic_dates(override_clinic, saturdays)
                default_req = 1          # デフォルトは通常

            if not clinic_sats:
                st.info("この外勤先は対象月に該当日がありません")
            else:
                OVERRIDE_OPTIONS = ["通常(1人)", "2人体制", "休診"]
                REQ_MAP = {"通常(1人)": 1, "2人体制": 2, "休診": 0}
                REQ_TO_LABEL = {1: "通常(1人)", 2: "2人体制", 0: "休診"}

                override_cols = st.columns(min(len(clinic_sats), 5))
                changes = {}
                for i, s in enumerate(clinic_sats):
                    ds = s.isoformat()
                    current_req = overrides.get((override_clinic["id"], ds), default_req)
                    current_label = REQ_TO_LABEL.get(current_req, "通常(1人)")
                    with override_cols[i % len(override_cols)]:
                        sel = st.radio(
                            s.strftime("%m/%d(%a)"),
                            OVERRIDE_OPTIONS,
                            index=OVERRIDE_OPTIONS.index(current_label),
                            key=f"ovr_{override_clinic['id']}_{ds}",
                        )
                        new_req = REQ_MAP[sel]
                        if new_req != current_req:
                            changes[(override_clinic["id"], ds)] = new_req

                if st.button("日別設定を保存", type="primary", key="save_overrides"):
                    if changes:
                        set_clinic_date_overrides_batch(changes)
                        st.session_state["_save_msg"] = f"「{override_clinic['name']}」の日別設定を保存しました（{len(changes)}件変更）"
                    else:
                        st.session_state["_save_msg"] = "変更はありませんでした"
                    st.rerun()

    # ---- セクション4: 土曜対象日の追加・除外 ----
    st.markdown("---")
    st.subheader("土曜対象日の追加・除外")
    st.caption("通常の土曜日に加え、翌月の日付を追加したり、年末年始等を除外できます")

    import calendar as _cal
    from datetime import date as _date

    # 対象月の全土曜を取得（追加/除外なしのベース）
    _base_sats = get_target_saturdays(year, month)
    _extra = get_saturday_extra_dates(target_month)
    _excluded = get_saturday_excluded_dates(target_month)

    st.write(f"**{target_month} のベース土曜日**: {', '.join(s.strftime('%m/%d') for s in _base_sats) if _base_sats else 'なし'}")
    if _extra:
        st.write(f"**追加日**: {', '.join(_extra)}")
    if _excluded:
        st.write(f"**除外日**: {', '.join(_excluded)}")

    with st.expander("追加・除外日の編集"):
        # 追加日の入力（翌月の土曜日候補を表示）
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        next_month_sats = []
        for day in range(1, _cal.monthrange(next_year, next_month)[1] + 1):
            d = _date(next_year, next_month, day)
            if d.weekday() == 5:  # Saturday
                next_month_sats.append(d)

        # 追加候補: 翌月の土曜日
        extra_options = [d.isoformat() for d in next_month_sats]
        current_extra = [d for d in _extra if d in extra_options]
        new_extra = st.multiselect(
            f"追加日（{next_year}-{next_month:02d}の土曜から選択）",
            options=extra_options,
            default=current_extra,
            key="sat_extra_dates",
        )

        # 除外候補: ベース土曜日
        exclude_options = [s.isoformat() for s in _base_sats]
        current_excluded = [d for d in _excluded if d in exclude_options]
        new_excluded = st.multiselect(
            f"除外日（{target_month}の土曜から選択）",
            options=exclude_options,
            default=current_excluded,
            key="sat_excluded_dates",
        )

        if st.button("土曜対象日を保存", type="primary", key="save_sat_dates"):
            set_saturday_extra_dates(target_month, new_extra)
            set_saturday_excluded_dates(target_month, new_excluded)
            st.session_state["_save_msg"] = "土曜対象日の設定を保存しました"
            st.rerun()

    # ---- セクション5: 平日外勤設定 ----
    st.markdown("---")
    st.subheader("平日外勤設定")
    st.caption("平日外勤セクション（医院ごと）の作成・編集・副管理者パスワード設定")

    DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金"}

    # 新規セクション追加
    with st.expander("セクションの追加", expanded=False):
        with st.form("add_weekday_section_form", clear_on_submit=True):
            new_clinic_name = st.text_input("医院名", placeholder="例: A医院")
            day_options = list(DAY_NAMES.items())
            new_days = st.multiselect(
                "曜日",
                options=[k for k, _ in day_options],
                format_func=lambda x: DAY_NAMES[x],
            )
            _all_docs = get_doctors()
            _doc_map = build_display_name_map(_all_docs)
            _doc_ids = [d["id"] for d in _all_docs]
            new_assigned = st.multiselect(
                "所属メンバー",
                options=_doc_ids,
                format_func=lambda x: _doc_map.get(x, "?"),
            )
            if st.form_submit_button("セクションを追加", use_container_width=True):
                if not new_clinic_name.strip():
                    st.error("医院名を入力してください")
                elif not new_days:
                    st.error("曜日を1つ以上選択してください")
                else:
                    add_weekday_config(new_clinic_name.strip(), new_days, new_assigned)
                    st.success(f"「{new_clinic_name}」を追加しました")
                    st.rerun()

    # 既存セクション一覧
    try:
        wk_configs = get_weekday_configs()
    except Exception:
        wk_configs = []

    if wk_configs:
        for cfg in wk_configs:
            section = cfg["section"]
            days_str = "・".join(DAY_NAMES.get(d, str(d)) for d in cfg.get("days_of_week", []))
            status = "有効" if cfg.get("is_active") else "無効"
            assigned = cfg.get("assigned_doctors", [])
            _all_docs_sec = get_doctors()
            _doc_map_sec = build_display_name_map(_all_docs_sec)
            assigned_names = ", ".join(_doc_map_sec.get(did, "?") for did in assigned) if assigned else "未設定"
            subadmins = cfg.get("subadmin_doctors", [])
            subadmin_names = ", ".join(_doc_map_sec.get(did, "?") for did in subadmins) if subadmins else "未設定"

            with st.container(border=True):
                st.markdown(f"**{cfg['clinic_name']}**　{days_str}曜日　{status}　メンバー: {assigned_names}　副管理者: {subadmin_names}")

                bc1, bc2, bc3, bc4 = st.columns(4)
                with bc1:
                    if cfg.get("is_active"):
                        if st.button("無効化", key=f"wk_deact_{section}", use_container_width=True):
                            update_weekday_config(section, is_active=False)
                            st.rerun()
                    else:
                        if st.button("有効化", key=f"wk_act_{section}", use_container_width=True):
                            update_weekday_config(section, is_active=True)
                            st.rerun()
                with bc2:
                    if st.button("編集", key=f"wk_edit_{section}", use_container_width=True):
                        st.session_state[f"wk_editing_{section}"] = True
                with bc3:
                    if st.button("副管理者設定", key=f"wk_subadmin_{section}", use_container_width=True):
                        st.session_state[f"wk_setting_subadmin_{section}"] = True
                with bc4:
                    if st.button("削除", key=f"wk_del_{section}", type="secondary", use_container_width=True):
                        st.session_state[f"wk_confirm_del_{section}"] = True

            # 編集フォーム
            if st.session_state.get(f"wk_editing_{section}"):
                with st.form(f"wk_edit_form_{section}"):
                    edit_name = st.text_input("医院名", value=cfg["clinic_name"], key=f"wk_name_{section}")
                    edit_days = st.multiselect(
                        "曜日",
                        options=[k for k, _ in list(DAY_NAMES.items())],
                        default=cfg.get("days_of_week", []),
                        format_func=lambda x: DAY_NAMES[x],
                        key=f"wk_days_{section}",
                    )
                    _all_docs_edit = get_doctors()
                    _doc_map_edit = build_display_name_map(_all_docs_edit)
                    _doc_ids_edit = [d["id"] for d in _all_docs_edit]
                    edit_assigned = st.multiselect(
                        "所属メンバー",
                        options=_doc_ids_edit,
                        default=[did for did in assigned if did in [d["id"] for d in _all_docs_edit]],
                        format_func=lambda x: _doc_map_edit.get(x, "?"),
                        key=f"wk_assigned_{section}",
                    )
                    fc1, fc2 = st.columns(2)
                    with fc1:
                        if st.form_submit_button("保存"):
                            if not edit_name.strip():
                                st.error("医院名を入力してください")
                            elif not edit_days:
                                st.error("曜日を1つ以上選択してください")
                            else:
                                update_weekday_config(
                                    section,
                                    clinic_name=edit_name.strip(),
                                    days_of_week=edit_days,
                                    assigned_doctors=edit_assigned,
                                )
                                st.session_state.pop(f"wk_editing_{section}", None)
                                st.success("保存しました")
                                st.rerun()
                    with fc2:
                        if st.form_submit_button("キャンセル"):
                            st.session_state.pop(f"wk_editing_{section}", None)
                            st.rerun()

            # 副管理者設定（医員を最大2名選択）
            if st.session_state.get(f"wk_setting_subadmin_{section}"):
                with st.form(f"wk_subadmin_form_{section}"):
                    st.caption("副管理者を最大2名まで指定できます")
                    _all_docs_sub = get_doctors()
                    _doc_map_sub = build_display_name_map(_all_docs_sub)
                    _doc_ids_sub = [d["id"] for d in _all_docs_sub]
                    current_subadmins = cfg.get("subadmin_doctors", [])
                    edit_subadmins = st.multiselect(
                        "副管理者",
                        options=_doc_ids_sub,
                        default=[did for did in current_subadmins if did in _doc_ids_sub],
                        format_func=lambda x: _doc_map_sub.get(x, "?"),
                        key=f"wk_subadmin_sel_{section}",
                    )
                    fc1, fc2 = st.columns(2)
                    with fc1:
                        if st.form_submit_button("保存"):
                            if len(edit_subadmins) > 2:
                                st.error("副管理者は最大2名までです")
                            else:
                                update_weekday_config(section, subadmin_doctors=edit_subadmins)
                                st.session_state.pop(f"wk_setting_subadmin_{section}", None)
                                st.session_state["_toast_msg"] = f"「{cfg['clinic_name']}」の副管理者を設定しました"
                                st.rerun()
                    with fc2:
                        if st.form_submit_button("キャンセル"):
                            st.session_state.pop(f"wk_setting_subadmin_{section}", None)
                            st.rerun()

            # 削除確認
            if st.session_state.get(f"wk_confirm_del_{section}"):
                st.warning(f"「{cfg['clinic_name']}」セクションを削除しますか？")
                dc1, dc2 = st.columns(2)
                with dc1:
                    if st.button("削除する", key=f"wk_do_del_{section}", type="primary"):
                        delete_weekday_config(section)
                        st.session_state.pop(f"wk_confirm_del_{section}", None)
                        st.success("削除しました")
                        st.rerun()
                with dc2:
                    if st.button("キャンセル", key=f"wk_cancel_del_{section}"):
                        st.session_state.pop(f"wk_confirm_del_{section}", None)
                        st.rerun()
    else:
        st.info("平日外勤セクションはまだ登録されていません。")
