"""管理者: 外勤先マスタタブ

サブタブ構成:
  - 外勤先台帳: 外勤先一覧（追加・編集）／優先度・指名マトリクス／掛け持ちペア
  - 月次設定  : 外勤先の日別設定（2人体制/休診）／土曜対象日の追加・除外
"""
import pandas as pd
import streamlit as st
from database import (
    get_doctors,
    get_clinics, add_clinic, update_clinic,
    get_affinities, batch_set_affinities,
    get_clinic_date_overrides, set_clinic_date_overrides_batch,
    get_saturday_extra_dates, set_saturday_extra_dates,
    get_saturday_excluded_dates, set_saturday_excluded_dates,
    get_double_shift_pairs, add_double_shift_pair, delete_double_shift_pair,
)
from optimizer import get_target_saturdays, get_clinic_dates
from components.display_utils import build_display_name_map, inject_master_css

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
    st.header("外勤先マスタ")
    inject_master_css()

    # 保存成功メッセージ（前回の保存結果を表示）
    if st.session_state.get("_save_msg"):
        st.toast(st.session_state.pop("_save_msg"))

    tab_ledger, tab_monthly = st.tabs(["外勤先台帳", "月次設定"])

    with tab_ledger:
        _render_clinic_list()
        st.markdown("---")
        _render_priority()
        st.markdown("---")
        _render_double_shift()

    with tab_monthly:
        _render_day_overrides(target_month, year, month)
        st.markdown("---")
        _render_saturday_dates(target_month, year, month)


# ==================== 外勤先台帳 ====================

def _render_clinic_list():
    """外勤先一覧（追加・編集）"""
    st.subheader("外勤先一覧")
    with st.expander("外勤先の追加", expanded=False):
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

    with st.expander("外勤先の編集", expanded=False):
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


def _render_priority():
    """外勤先の指名・優先度設定（医員×外勤先マトリクス）"""
    st.subheader("外勤先の指名・優先度設定")

    clinics = get_clinics()
    doctors = get_doctors()

    if clinics and doctors:
        all_affinities = get_affinities()

        # 医員のソート: job_rank降順 → 名前順（上級医が上）
        rank_labels = {0: "未設定", 1: "レジデント", 2: "大学院生", 3: "フェロー"}
        sorted_doctors = sorted(doctors, key=lambda d: (-d.get("job_rank", 0), d["name"]))
        _display_map = build_display_name_map(doctors)

        # --- 優先度マトリクス（編集可能）---
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

        # --- 確認ビュー ---
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


def _render_double_shift():
    """掛け持ちペア設定（AM→PM の外勤先組み合わせ）"""
    st.subheader("掛け持ちペア設定")
    st.caption(
        "午前の外勤先と午後の外勤先の組み合わせを登録します。"
        "通常の割り当てで解がない場合のみ、同一日に2か所の外勤が許可されます。"
    )

    clinics_all = get_clinics(active_only=True)
    clinic_name_map = {c["id"]: c["name"] for c in clinics_all}
    clinic_time_info = {}
    for c in clinics_all:
        parts = []
        if c.get("start_time") and c.get("end_time"):
            parts.append(f"{c['start_time']}〜{c['end_time']}")
        if c.get("time_slot"):
            parts.append(c["time_slot"])
        clinic_time_info[c["id"]] = " ".join(parts)

    # 既存ペア一覧
    existing_pairs = get_double_shift_pairs(active_only=False)
    if existing_pairs:
        for p in existing_pairs:
            am_name = clinic_name_map.get(p["am_clinic_id"], f"ID:{p['am_clinic_id']}")
            pm_name = clinic_name_map.get(p["pm_clinic_id"], f"ID:{p['pm_clinic_id']}")
            am_time = clinic_time_info.get(p["am_clinic_id"], "")
            pm_time = clinic_time_info.get(p["pm_clinic_id"], "")
            pcol1, pcol2 = st.columns([4, 1])
            with pcol1:
                st.write(f"**{am_name}** ({am_time}) → **{pm_name}** ({pm_time})")
            with pcol2:
                if st.button("削除", key=f"del_dsp_{p['id']}"):
                    delete_double_shift_pair(p["id"])
                    st.rerun()
    else:
        st.info("掛け持ちペアは登録されていません")

    # 新規追加
    with st.expander("掛け持ちペアを追加"):
        def _clinic_label(c):
            parts = [c["name"]]
            if c.get("start_time") and c.get("end_time"):
                parts.append(f"({c['start_time']}〜{c['end_time']})")
            elif c.get("time_slot"):
                parts.append(f"({c['time_slot']})")
            return " ".join(parts)

        clinic_options = {_clinic_label(c): c["id"] for c in clinics_all}

        sel_am = st.selectbox("1つ目の外勤先", options=list(clinic_options.keys()), key="dsp_am")
        sel_pm = st.selectbox("2つ目の外勤先", options=list(clinic_options.keys()), key="dsp_pm")

        if st.button("ペアを追加", key="add_dsp"):
            am_id = clinic_options[sel_am]
            pm_id = clinic_options[sel_pm]
            if am_id == pm_id:
                st.error("同じ外勤先は選択できません")
            else:
                add_double_shift_pair(am_id, pm_id)
                st.success("掛け持ちペアを追加しました")
                st.rerun()


# ==================== 月次設定 ====================

def _render_day_overrides(target_month, year, month):
    """外勤先の日別設定（2人体制/休診）"""
    st.subheader("外勤先の日別設定")
    st.caption("特定の日に2人体制にする、または休診に設定できます")

    clinics = get_clinics()
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


def _render_saturday_dates(target_month, year, month):
    """土曜対象日の追加・除外"""
    st.subheader("土曜対象日の追加・除外")
    st.caption("通常の土曜日に加え、翌月の日付を追加したり、年末年始等を除外できます")

    import calendar as _cal
    from datetime import date as _date

    # 対象月の全土曜を取得（追加/除外なしのベース）
    _base_sats = get_target_saturdays(year, month, base_only=True)
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
