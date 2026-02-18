"""管理者: マスタ管理タブ"""
import streamlit as st
from database import (
    get_doctors, add_doctor, update_doctor, delete_doctor,
    get_clinics, add_clinic, update_clinic, delete_clinic,
    get_affinities, set_affinity,
    get_clinic_date_overrides, set_clinic_date_overrides_batch,
    set_doctor_individual_password, update_doctor_email,
    get_open_month, set_open_month,
)
from optimizer import get_target_saturdays, get_clinic_dates
from datetime import date
from dateutil.relativedelta import relativedelta


def _render_open_month_setting():
    """希望入力の対象月を設定するUI"""
    st.subheader("希望入力 対象月設定")
    current = get_open_month()
    if current:
        st.write(f"現在の対象月: **{current}**")
    else:
        st.warning("対象月が未設定です。医員は希望入力できません。")

    today = date.today()
    month_options = [
        (today + relativedelta(months=i)).strftime("%Y-%m") for i in range(4)
    ]
    col1, col2 = st.columns([3, 1])
    with col1:
        selected = st.selectbox(
            "対象月を選択", month_options,
            index=month_options.index(current) if current in month_options else 0,
            key="open_month_select",
            label_visibility="collapsed",
        )
    with col2:
        if st.button("設定", key="set_open_month", use_container_width=True):
            set_open_month(selected)
            st.success(f"対象月を {selected} に設定しました")
            st.rerun()


FREQ_OPTIONS = [
    ("weekly", "毎週"),
    ("biweekly_odd", "隔週（奇数週）"),
    ("biweekly_even", "隔週（偶数週）"),
    ("first_only", "第1週のみ"),
    ("last_only", "最終週のみ"),
]
FREQ_LABELS = {k: v for k, v in FREQ_OPTIONS}


def render(target_month, year, month):
    st.header("マスタ管理")

    # ---- 希望入力 対象月設定 ----
    _render_open_month_setting()
    st.markdown("---")

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
                new_doc = st.text_input("新規医員名")
                if st.form_submit_button("追加", use_container_width=True):
                    if new_doc.strip():
                        add_doctor(new_doc.strip())
                        st.success(f"「{new_doc}」を追加しました")
                        st.rerun()

            doctors_all = get_doctors(active_only=False)
            if doctors_all:
                def _doc_label(d):
                    s = "有効" if d["is_active"] else "無効"
                    pw = "🔑" if d.get("password_hash") else "⚠️"
                    return f"{d['name']}（{s}）{pw}"

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
                    email_display = d.get("email", "") or "未設定"
                    max_a = d.get("max_assignments", 0)
                    limit_display = f"{max_a}回/月" if max_a > 0 else "制限なし"
                    with st.container(border=True):
                        st.markdown(f'<span class="{marker}"></span>', unsafe_allow_html=True)
                        st.markdown(f"**{d['name']}**　{status_label}　📧 {email_display}　上限: {limit_display}")
                        b1, b2, b3, b4, b5, b6 = st.columns(6)
                        with b1:
                            if d['is_active']:
                                if st.button("無効化", key=f"deact_{d['id']}", type="secondary", use_container_width=True):
                                    update_doctor(d['id'], is_active=0)
                                    st.rerun()
                            else:
                                if st.button("有効化", key=f"act_{d['id']}", use_container_width=True):
                                    update_doctor(d['id'], is_active=1)
                                    st.rerun()
                        with b2:
                            if st.button("名前変更", key=f"rename_{d['id']}", use_container_width=True):
                                st.session_state[f"editing_doc_{d['id']}"] = True
                        with b3:
                            btn_label = "PW再設定" if has_pw else "PW設定"
                            if st.button(btn_label, key=f"setpw_{d['id']}", use_container_width=True):
                                st.session_state[f"setting_pw_{d['id']}"] = True
                        with b4:
                            email_btn = "📧変更" if has_email else "📧設定"
                            if st.button(email_btn, key=f"setemail_{d['id']}", use_container_width=True):
                                st.session_state[f"setting_email_{d['id']}"] = True
                        with b5:
                            if st.button("回数上限", key=f"setlimit_{d['id']}", use_container_width=True):
                                st.session_state[f"setting_limit_{d['id']}"] = True
                        with b6:
                            if st.button("削除", key=f"del_doc_{d['id']}", type="secondary", use_container_width=True):
                                st.session_state[f"confirm_del_doc_{d['id']}"] = True

                    # 名前変更フォーム
                    if st.session_state.get(f"editing_doc_{d['id']}"):
                        with st.form(f"rename_form_{d['id']}"):
                            new_name = st.text_input("新しい名前", value=d["name"])
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("保存"):
                                    if new_name.strip() and new_name.strip() != d["name"]:
                                        update_doctor(d['id'], name=new_name.strip())
                                        st.success("名前を変更しました")
                                    st.session_state.pop(f"editing_doc_{d['id']}", None)
                                    st.rerun()
                            with fc2:
                                if st.form_submit_button("キャンセル"):
                                    st.session_state.pop(f"editing_doc_{d['id']}", None)
                                    st.rerun()

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
                                        st.success(f"「{d['name']}」のパスワードを設定しました")
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
                                    st.success(f"「{d['name']}」のメールアドレスを保存しました")
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
                                st.success("削除しました")
                                st.rerun()
                        with dc2:
                            if st.button("キャンセル", key=f"cancel_del_doc_{d['id']}"):
                                st.session_state.pop(f"confirm_del_doc_{d['id']}", None)
                                st.rerun()

                    # 回数上限設定フォーム
                    if st.session_state.get(f"setting_limit_{d['id']}"):
                        with st.form(f"setlimit_form_{d['id']}"):
                            new_limit = st.number_input(
                                "月回数上限（0 = 制限なし）",
                                min_value=0, max_value=20, value=max_a,
                                key=f"limit_val_{d['id']}"
                            )
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("保存"):
                                    update_doctor(d['id'], max_assignments=new_limit)
                                    lbl = "制限なし" if new_limit == 0 else f"{new_limit}回/月"
                                    st.success(f"回数上限を{lbl}に設定しました")
                                    st.session_state.pop(f"setting_limit_{d['id']}", None)
                                    st.rerun()
                            with fc2:
                                if st.form_submit_button("キャンセル"):
                                    st.session_state.pop(f"setting_limit_{d['id']}", None)
                                    st.rerun()

    # ---- 外勤先管理 ----
    with col2:
        st.subheader("外勤先一覧")
        with st.expander("外勤先の追加・編集", expanded=False):
            with st.form("add_clinic_form", clear_on_submit=True):
                new_clinic = st.text_input("外勤先名")
                new_fee = st.number_input("日当（円）", min_value=0, step=10000, value=50000)
                new_freq = st.selectbox("頻度", FREQ_OPTIONS, format_func=lambda x: x[1])
                if st.form_submit_button("追加", use_container_width=True):
                    if new_clinic.strip():
                        add_clinic(new_clinic.strip(), new_fee, new_freq[0])
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
                    with st.container(border=True):
                        st.markdown(f'<span class="{marker}"></span>', unsafe_allow_html=True)
                        st.markdown(
                            f"**{c['name']}**　{status_label} | ¥{c['fee']:,} | "
                            f"{FREQ_LABELS.get(c['frequency'], c['frequency'])}"
                        )
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
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                if st.form_submit_button("保存"):
                                    update_clinic(c['id'], fee=edit_fee, frequency=edit_freq[0])
                                    st.session_state.pop(f"editing_cli_{c['id']}", None)
                                    st.success("保存しました")
                                    st.rerun()
                            with fc2:
                                if st.form_submit_button("キャンセル"):
                                    st.session_state.pop(f"editing_cli_{c['id']}", None)
                                    st.rerun()

    # ---- 指名・優先度設定 ----
    st.markdown("---")
    st.subheader("指名・優先度設定")

    clinics = get_clinics()
    doctors = get_doctors()

    PRIORITY_OPTIONS = {"◎ 必ず行く": 2.0, "○ 行くときもある": 1.0, "× 行かない": 0.0}
    WEIGHT_TO_LABEL = {2.0: "◎ 必ず行く", 1.0: "○ 行くときもある", 0.0: "× 行かない"}

    # 保存成功メッセージ（前回の保存結果を表示）
    _msg_area = st.empty()
    if st.session_state.get("_save_msg"):
        _msg_area.success(st.session_state.pop("_save_msg"))

    if clinics and doctors:
        pri_tab1, pri_tab2 = st.tabs(["外勤先から設定", "医員から設定"])

        all_affinities = get_affinities()

        # ---- タブ1: 外勤先別（外勤先を選んで各医員の優先度を設定）----
        with pri_tab1:
            selected_clinic = st.selectbox(
                "外勤先を選択",
                clinics,
                format_func=lambda c: c["name"],
                key="affinity_clinic"
            )

            if selected_clinic:
                # 指名医員
                pref_docs = selected_clinic.get("preferred_doctors", [])
                st.write("**指名医員（この外勤先が希望する医員）:**")
                new_pref = st.multiselect(
                    "指名医員",
                    [d["id"] for d in doctors],
                    default=[did for did in pref_docs if did in [d["id"] for d in doctors]],
                    format_func=lambda did: next((d["name"] for d in doctors if d["id"] == did), str(did)),
                    label_visibility="collapsed"
                )
                if st.button("指名を保存", type="primary", key="save_nomination"):
                    update_clinic(selected_clinic["id"], preferred_doctors=new_pref)
                    st.session_state["_save_msg"] = f"「{selected_clinic['name']}」の指名医員を保存しました"
                    st.rerun()

                # 優先度（外勤先 → 各医員）
                st.write("**各医員の優先度:**")
                st.caption("◎ 月1回以上必ず行く ／ ○ 行くときもある ／ × まったく行かない")
                current_affinities = {
                    a["doctor_id"]: a["weight"]
                    for a in all_affinities
                    if a["clinic_id"] == selected_clinic["id"]
                }

                aff_cols = st.columns(4)
                for i, d in enumerate(doctors):
                    with aff_cols[i % 4]:
                        current_w = current_affinities.get(d["id"], 1.0)
                        current_label = WEIGHT_TO_LABEL.get(current_w, "○ 行くときもある")
                        st.radio(
                            d["name"],
                            list(PRIORITY_OPTIONS.keys()),
                            index=list(PRIORITY_OPTIONS.keys()).index(current_label),
                            key=f"pri_{selected_clinic['id']}_{d['id']}",
                            horizontal=True,
                        )

                if st.button("優先度を保存", type="primary", key="save_affinity_by_clinic"):
                    changed = 0
                    for d in doctors:
                        sel_label = st.session_state.get(f"pri_{selected_clinic['id']}_{d['id']}")
                        if sel_label is None:
                            continue
                        new_w = PRIORITY_OPTIONS[sel_label]
                        old_w = current_affinities.get(d["id"], 1.0)
                        if new_w != old_w:
                            set_affinity(d["id"], selected_clinic["id"], new_w)
                            changed += 1
                    if changed:
                        st.session_state["_save_msg"] = f"「{selected_clinic['name']}」の優先度を保存しました（{changed}件変更）"
                    else:
                        st.session_state["_save_msg"] = "変更はありませんでした"
                    st.rerun()

        # ---- タブ2: 医員別（医員を選んで各外勤先の優先度を設定）----
        with pri_tab2:
            selected_doctor = st.selectbox(
                "医員を選択",
                doctors,
                format_func=lambda doc: doc["name"],
                key="affinity_doctor"
            )

            if selected_doctor:
                st.write(f"**{selected_doctor['name']}** の各外勤先への優先度:")
                st.caption("◎ 月1回以上必ず行く ／ ○ 行くときもある ／ × まったく行かない")
                doc_affinities = {
                    a["clinic_id"]: a["weight"]
                    for a in all_affinities
                    if a["doctor_id"] == selected_doctor["id"]
                }

                with st.form(f"affinity_form_doc_{selected_doctor['id']}"):
                    n_cols = min(len(clinics), 4)
                    aff_cols2 = st.columns(n_cols)
                    for i, cli in enumerate(clinics):
                        with aff_cols2[i % n_cols]:
                            current_w = doc_affinities.get(cli["id"], 1.0)
                            current_label = WEIGHT_TO_LABEL.get(current_w, "○ 行くときもある")
                            st.radio(
                                cli["name"],
                                list(PRIORITY_OPTIONS.keys()),
                                index=list(PRIORITY_OPTIONS.keys()).index(current_label),
                                key=f"pri_doc_{selected_doctor['id']}_{cli['id']}",
                                horizontal=True,
                            )

                    if st.form_submit_button("優先度を保存", type="primary"):
                        changed = 0
                        for cli in clinics:
                            sel_label = st.session_state.get(f"pri_doc_{selected_doctor['id']}_{cli['id']}")
                            if sel_label is None:
                                continue
                            new_w = PRIORITY_OPTIONS[sel_label]
                            old_w = doc_affinities.get(cli["id"], 1.0)
                            if new_w != old_w:
                                set_affinity(selected_doctor["id"], cli["id"], new_w)
                                changed += 1
                        if changed:
                            st.session_state["_save_msg"] = f"「{selected_doctor['name']}」の優先度を保存しました（{changed}件変更）"
                        else:
                            st.session_state["_save_msg"] = "変更はありませんでした"
                        st.rerun()

    # ---- 外勤先の日別設定 ----
    st.markdown("---")
    st.subheader(f"外勤先の日別設定 ({target_month})")
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
            clinic_sats = get_clinic_dates(override_clinic, saturdays)
            overrides = get_clinic_date_overrides(target_month)

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
                    current_req = overrides.get((override_clinic["id"], ds), 1)
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
