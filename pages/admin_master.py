"""管理者: マスタ管理タブ"""
import json
import streamlit as st
from database import (
    get_doctors, add_doctor, update_doctor, delete_doctor,
    get_clinics, add_clinic, update_clinic, delete_clinic,
    get_affinities, set_affinity,
    get_clinic_date_overrides, set_clinic_date_override,
    is_doctor_password_set, set_doctor_password,
)
from optimizer import get_target_saturdays, get_clinic_dates


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

    # ---- 医員用パスワード設定 ----
    with st.expander("医員用パスワード設定"):
        if is_doctor_password_set():
            st.write("設定済み")
        else:
            st.warning("未設定（医員がログインできません）")
        pw1 = st.text_input("新しいパスワード", type="password", key="doc_pw1")
        pw2 = st.text_input("パスワード（確認）", type="password", key="doc_pw2")
        if st.button("医員用パスワードを設定"):
            if not pw1:
                st.error("パスワードを入力してください")
            elif pw1 != pw2:
                st.error("パスワードが一致しません")
            else:
                set_doctor_password(pw1)
                st.success("医員用パスワードを設定しました")
                st.rerun()

    col1, col2 = st.columns(2)

    # ---- 医員管理 ----
    with col1:
        st.subheader("医員一覧")
        with st.form("add_doctor_form"):
            new_doc = st.text_input("新規医員名")
            if st.form_submit_button("追加", use_container_width=True):
                if new_doc.strip():
                    add_doctor(new_doc.strip())
                    st.success(f"「{new_doc}」を追加しました")
                    st.rerun()

        doctors_all = get_doctors(active_only=False)
        if doctors_all:
            for d in doctors_all:
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                with c1:
                    st.write(f"{'[有効]' if d['is_active'] else '[無効]'} {d['name']}")
                with c2:
                    if d['is_active']:
                        if st.button("無効化", key=f"deact_{d['id']}", type="secondary"):
                            update_doctor(d['id'], is_active=0)
                            st.rerun()
                    else:
                        if st.button("有効化", key=f"act_{d['id']}"):
                            update_doctor(d['id'], is_active=1)
                            st.rerun()
                with c3:
                    if st.button("名前変更", key=f"rename_{d['id']}"):
                        st.session_state[f"editing_doc_{d['id']}"] = True
                with c4:
                    if st.button("削除", key=f"del_doc_{d['id']}", type="secondary"):
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

    # ---- 外勤先管理 ----
    with col2:
        st.subheader("外勤先一覧")
        with st.form("add_clinic_form"):
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
            for c in clinics_all:
                cc1, cc2, cc3 = st.columns([4, 1, 1])
                with cc1:
                    st.write(
                        f"{'[有効]' if c['is_active'] else '[無効]'} **{c['name']}** "
                        f"| ¥{c['fee']:,} | {FREQ_LABELS.get(c['frequency'], c['frequency'])}"
                    )
                with cc2:
                    if c['is_active']:
                        if st.button("無効化", key=f"deact_cli_{c['id']}", type="secondary"):
                            update_clinic(c['id'], is_active=0)
                            st.rerun()
                    else:
                        if st.button("有効化", key=f"act_cli_{c['id']}"):
                            update_clinic(c['id'], is_active=1)
                            st.rerun()
                with cc3:
                    if st.button("編集", key=f"edit_cli_{c['id']}"):
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

    # ---- 外勤先の指名・優先度設定 ----
    st.markdown("---")
    st.subheader("外勤先の指名・優先度設定")

    clinics = get_clinics()
    doctors = get_doctors()

    if clinics and doctors:
        selected_clinic = st.selectbox(
            "外勤先を選択",
            clinics,
            format_func=lambda c: c["name"],
            key="affinity_clinic"
        )

        if selected_clinic:
            pref_docs = json.loads(selected_clinic.get("preferred_doctors", "[]"))

            st.write("**指名医員（この外勤先が希望する医員）:**")
            new_pref = st.multiselect(
                "指名医員",
                [d["id"] for d in doctors],
                default=[did for did in pref_docs if did in [d["id"] for d in doctors]],
                format_func=lambda did: next((d["name"] for d in doctors if d["id"] == did), str(did)),
                label_visibility="collapsed"
            )
            if st.button("指名を保存"):
                update_clinic(selected_clinic["id"], preferred_doctors=new_pref)
                st.success("保存しました")
                st.rerun()

            st.write("**医員別 優先度:**")
            st.caption("◎ 月1回以上必ず行く ／ ○ 行くときもある ／ × まったく行かない")
            current_affinities = {
                a["doctor_id"]: a["weight"]
                for a in get_affinities()
                if a["clinic_id"] == selected_clinic["id"]
            }

            PRIORITY_OPTIONS = {"○ 行くときもある": 1.0, "◎ 必ず行く": 2.0, "× 行かない": 0.0}
            WEIGHT_TO_LABEL = {2.0: "◎ 必ず行く", 1.0: "○ 行くときもある", 0.0: "× 行かない"}

            aff_cols = st.columns(4)
            for i, d in enumerate(doctors):
                with aff_cols[i % 4]:
                    current_w = current_affinities.get(d["id"], 1.0)
                    current_label = WEIGHT_TO_LABEL.get(current_w, "○ 行くときもある")
                    selected = st.radio(
                        d["name"],
                        list(PRIORITY_OPTIONS.keys()),
                        index=list(PRIORITY_OPTIONS.keys()).index(current_label),
                        key=f"pri_{selected_clinic['id']}_{d['id']}",
                        horizontal=True,
                    )
                    new_w = PRIORITY_OPTIONS[selected]
                    if new_w != current_w:
                        set_affinity(d["id"], selected_clinic["id"], new_w)

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
                    for (cid, ds), req in changes.items():
                        set_clinic_date_override(cid, ds, req)
                    st.success("保存しました")
                    st.rerun()
