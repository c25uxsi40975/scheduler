"""管理者: 医員マスタタブ

サブタブ構成:
  - 医員台帳: 医員一覧（追加・編集・PW・メール・役職・削除）／月回数上限の一括設定
  - 希望状況: 希望状況一覧（読み取り概要）／日程希望の代理入力／個別の外勤先希望・備考
"""
import pandas as pd
import streamlit as st
from database import (
    get_doctors, add_doctor, update_doctor, delete_doctor,
    set_doctor_individual_password, update_doctor_email,
    batch_update_max_assignments,
    get_clinics,
    get_all_preferences, upsert_preference, batch_upsert_preferences,
)
from optimizer import get_target_saturdays
from components.display_utils import build_display_name_map, inject_master_css


def render(target_month, year, month):
    if not st.session_state.get("admin_authenticated"):
        st.stop()
    st.header("医員マスタ")
    inject_master_css()

    # 保存成功メッセージ（前回の保存結果を表示）
    if st.session_state.get("_save_msg"):
        st.toast(st.session_state.pop("_save_msg"))

    tab_ledger, tab_pref = st.tabs(["医員台帳", "希望状況"])

    with tab_ledger:
        _render_doctor_list()
        st.markdown("---")
        _render_max_assignments()

    with tab_pref:
        _render_pref_overview(target_month, year, month)
        st.markdown("---")
        _render_pref_matrix(target_month, year, month)
        st.markdown("---")
        _render_pref_detail(target_month, year, month)


# ==================== 医員台帳 ====================

def _render_doctor_list():
    """医員一覧（追加・編集・PW・メール・役職・削除）"""
    st.subheader("医員一覧")
    with st.expander("医員の追加", expanded=False):
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

    with st.expander("医員の編集", expanded=False):
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


def _render_max_assignments():
    """月回数上限の一括設定"""
    doctors = get_doctors()
    if not doctors:
        return
    st.subheader("月回数上限の一括設定")
    st.caption("各医員の月あたりの最大外勤回数を設定します（1〜4回）")

    _dmap = build_display_name_map(doctors)
    with st.form("batch_max_assignments"):
        max_cols = st.columns(min(len(doctors), 4))
        for i, d in enumerate(sorted(doctors, key=lambda d: (-d.get("job_rank", 0), d["name"]))):
            rank_labels = {0: "未設定", 1: "レジ", 2: "院生", 3: "フェロー"}
            with max_cols[i % len(max_cols)]:
                current_max = d.get("max_assignments", 0)
                if current_max < 1 or current_max > 4:
                    current_max = 4
                st.number_input(
                    f"{_dmap.get(d['id'], d['name'])}({rank_labels.get(d.get('job_rank', 0), '')})",
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


# ==================== 希望状況 ====================

def _render_pref_overview(target_month, year, month):
    """希望状況一覧（読み取り専用の概要）"""
    st.subheader(f"希望状況一覧 ({target_month})")

    doctors = get_doctors()
    clinics = get_clinics()
    clinic_map = {c["id"]: c["name"] for c in clinics}
    prefs = get_all_preferences(target_month)
    pref_map = {p["doctor_id"]: p for p in prefs}
    _dmap = build_display_name_map(doctors)

    saturdays = get_target_saturdays(year, month)
    sat_strs = [s.strftime("%m/%d") for s in saturdays]

    if not doctors:
        st.warning("医員が登録されていません。「医員台帳」で追加してください。")
        return

    data = []
    for d in doctors:
        p = pref_map.get(d["id"])
        row = {"医員": _dmap.get(d["id"], d["name"]), "入力済": "済" if p else "-"}
        if p:
            ng = set(p.get("ng_dates", []))
            avoid = set(p.get("avoid_dates", []))
            post_night = set(p.get("post_night_dates") or [])
            dcr = p.get("date_clinic_requests", {})
            for s, s_str in zip(saturdays, sat_strs):
                ds = s.isoformat()
                if ds in ng:
                    mark = "×"
                elif ds in avoid:
                    mark = "△"
                elif ds in post_night:
                    mark = "○(明)"
                else:
                    mark = "○"
                # 日別外勤先希望がある場合は追記
                if ds in dcr:
                    cid = dcr[ds]
                    if isinstance(cid, str):
                        cid = int(cid) if cid.isdigit() else cid
                    cname = clinic_map.get(cid, "?")
                    mark += f"({cname})"
                row[s_str] = mark
            row["備考"] = p.get("free_text", "")
        else:
            for s_str in sat_strs:
                row[s_str] = "-"
            row["備考"] = ""
        data.append(row)

    df = pd.DataFrame(data)
    st.caption("○ 可能 ／ ○(明) 当直明け○(PMのみ) ／ △ できれば避けたい ／ × NG")
    st.dataframe(df, use_container_width=True, hide_index=True)

    submitted = sum(1 for _ in pref_map.values())
    st.info(f"入力済: {submitted}/{len(doctors)}人")

    # 備考が入力されている医員の詳細表示
    docs_with_notes = [
        (_dmap.get(d["id"], d["name"]), pref_map[d["id"]].get("free_text", ""))
        for d in doctors
        if d["id"] in pref_map and pref_map[d["id"]].get("free_text")
    ]
    if docs_with_notes:
        st.markdown("**備考一覧**")
        for name, text in docs_with_notes:
            st.write(f"**{name}**: {text}")


def _render_pref_matrix(target_month, year, month):
    """日程希望の代理入力（医員×日付 ○/当○/△/×）"""
    st.subheader("日程希望 — 代理入力")
    st.caption("管理者が医員の日程希望をまとめて入力できます（○=可能 当○=当直明け(PMのみ) △=できれば避けたい ×=NG）")

    doctors = get_doctors()
    if not doctors:
        return

    saturdays = get_target_saturdays(year, month)
    if not saturdays:
        st.info("対象月に土曜日がありません")
        return

    prefs_3b = get_all_preferences(target_month)
    pref_map_3b = {p["doctor_id"]: p for p in prefs_3b}

    SCHEDULE_STATUS = ["○", "当○", "△", "×"]
    rank_labels_3b = {0: "未設定", 1: "レジ", 2: "院生", 3: "フェロー"}
    sorted_docs_3b = sorted(doctors, key=lambda d: (-d.get("job_rank", 0), d["name"]))
    _dmap_3b = build_display_name_map(doctors)

    # 入力済 / 未入力の分類
    submitted_doc_ids = set(pref_map_3b.keys())
    unsubmitted_docs = [d for d in sorted_docs_3b if d["id"] not in submitted_doc_ids]
    submitted_docs = [d for d in sorted_docs_3b if d["id"] in submitted_doc_ids]

    st.caption(f"入力済み: {len(submitted_docs)}名 / 未入力: {len(unsubmitted_docs)}名")

    # 表示切替
    show_mode = st.radio(
        "表示対象",
        ["未入力のみ", "全員"],
        horizontal=True,
        key="schedule_matrix_mode",
    )
    display_docs = unsubmitted_docs if show_mode == "未入力のみ" else sorted_docs_3b

    if not display_docs:
        st.success("全員入力済みです")
        return

    # DataFrame 構築
    matrix_data = {}
    for d in display_docs:
        pref = pref_map_3b.get(d["id"])
        submitted = pref is not None
        suffix = " 【済】" if submitted else ""
        row_label = f"{_dmap_3b.get(d['id'], d['name'])}({rank_labels_3b.get(d.get('job_rank', 0), '')}){suffix}"
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
            elif submitted:
                row[col_label] = "○"
            else:
                row[col_label] = "-"
        matrix_data[row_label] = row

    df_schedule = pd.DataFrame.from_dict(matrix_data, orient="index")
    schedule_col_config = {
        col: st.column_config.SelectboxColumn(
            col, options=["-"] + SCHEDULE_STATUS, default="-", width="small",
        )
        for col in df_schedule.columns
    }
    edited_schedule_df = st.data_editor(
        df_schedule,
        column_config=schedule_col_config,
        use_container_width=True,
        key="schedule_matrix",
    )

    # ---- ヘルパー: 編集結果から batch_items を構築 ----
    def _build_batch_items(target_docs):
        """target_docs に含まれる医員の変更を収集"""
        items = []
        for d in target_docs:
            pref = pref_map_3b.get(d["id"])
            submitted = pref is not None
            suffix = " 【済】" if submitted else ""
            row_label = f"{_dmap_3b.get(d['id'], d['name'])}({rank_labels_3b.get(d.get('job_rank', 0), '')}){suffix}"
            if row_label not in edited_schedule_df.index:
                continue
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
                # "-" と "○" は何もリストに追加しない（=可能）

            if not pref or set(new_ng) != old_ng or set(new_avoid) != old_avoid or set(new_pn) != old_pn:
                items.append({
                    "doctor_id": d["id"],
                    "ng_dates": new_ng,
                    "avoid_dates": new_avoid,
                    "post_night_dates": new_pn,
                    "preferred_clinics": pref.get("preferred_clinics", []) if pref else [],
                    "date_clinic_requests": pref.get("date_clinic_requests", {}) if pref else {},
                    "free_text": pref.get("free_text", "") if pref else "",
                })
        return items

    # ---- 保存ボタン ----
    btn_cols = st.columns(2)
    with btn_cols[0]:
        if st.button(
            f"未入力のみ代行保存（{len(unsubmitted_docs)}名）",
            type="primary",
            key="save_schedule_unsubmitted",
            disabled=len(unsubmitted_docs) == 0,
        ):
            batch_items = _build_batch_items(unsubmitted_docs)
            if batch_items:
                batch_upsert_preferences(target_month, batch_items)
                st.session_state["_save_msg"] = f"未入力者の日程希望を保存しました（{len(batch_items)}名）"
            else:
                st.session_state["_save_msg"] = "変更はありませんでした"
            st.rerun()
    with btn_cols[1]:
        if st.button("全員を一括保存", key="save_schedule_matrix"):
            batch_items = _build_batch_items(display_docs)
            if batch_items:
                batch_upsert_preferences(target_month, batch_items)
                st.session_state["_save_msg"] = f"日程希望を保存しました（{len(batch_items)}名変更）"
            else:
                st.session_state["_save_msg"] = "変更はありませんでした"
            st.rerun()


def _render_pref_detail(target_month, year, month):
    """個別の外勤先希望・備考"""
    st.subheader(f"個別の外勤先希望・備考 ({target_month})")
    st.caption("医員ごとに「この日にこの外勤先に行きたい」希望と備考を設定できます")

    clinics = get_clinics()
    doctors = get_doctors()

    if not (clinics and doctors):
        return

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
            return

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
