"""管理者: 平日外勤セクション管理タブ"""
import streamlit as st
from database import (
    get_doctors,
    get_weekday_configs, create_weekday_spreadsheet,
    add_weekday_config, update_weekday_config, delete_weekday_config,
)
from components.display_utils import build_display_name_map


DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金"}


def render():
    st.subheader("平日外勤セクション管理")
    st.caption("平日外勤セクション（医院ごと）の作成・編集・副管理者設定")

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
            st.info("スプレッドシートはGAS経由で自動作成されます。")
            if st.form_submit_button("セクションを追加", use_container_width=True):
                if not new_clinic_name.strip():
                    st.error("医院名を入力してください")
                elif not new_days:
                    st.error("曜日を1つ以上選択してください")
                else:
                    try:
                        with st.spinner("スプレッドシートを作成中..."):
                            ss_key = create_weekday_spreadsheet(
                                f"外勤調整_平日_{new_clinic_name.strip()}"
                            )
                        add_weekday_config(
                            new_clinic_name.strip(), new_days, new_assigned,
                            spreadsheet_key=ss_key,
                        )
                        st.success(f"「{new_clinic_name}」を追加しました（スプレッドシート自動作成済み）")
                        st.rerun()
                    except Exception as e:
                        st.error(f"セクション追加に失敗しました: {e}")

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
                ss_key = cfg.get("spreadsheet_key", "")
                st.markdown(f"**{cfg['clinic_name']}**　{days_str}曜日　{status}　メンバー: {assigned_names}　副管理者: {subadmin_names}")
                if ss_key:
                    st.caption(f"スプレッドシート: `{ss_key}`")
                else:
                    st.warning("スプレッドシートが未設定です。")

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
                    edit_ss_key = st.text_input(
                        "スプレッドシートID",
                        value=cfg.get("spreadsheet_key", ""),
                        key=f"wk_sskey_{section}",
                        disabled=True,
                        help="自動作成されたスプレッドシートID（変更不可）",
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
                st.warning(f"「{cfg['clinic_name']}」セクションを削除しますか？関連するスロット・対象日も削除されます。")
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
