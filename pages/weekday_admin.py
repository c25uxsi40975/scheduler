"""
副管理者UI（平日外勤管理）
セクションパラメータで各医院共通のUIを提供
主管理者も admin_type でセクション指定してアクセス可能
"""
import json
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import streamlit as st

from database import (
    get_doctors,
    get_weekday_configs, get_weekday_config_by_section, update_weekday_config,
    get_weekday_slots, add_weekday_slot, update_weekday_slot, delete_weekday_slot,
    get_target_dates as db_get_target_dates, get_active_target_dates,
    set_target_dates, toggle_target_date,
    get_weekday_preferences, get_weekday_preference, upsert_weekday_preference,
    get_weekday_schedule, batch_save_weekday_assignments, delete_weekday_assignment,
    get_weekday_open_section, set_weekday_open_section,
    get_weekday_deadline, set_weekday_deadline,
    get_weekday_slot_overrides, set_weekday_slot_overrides_batch,
)
from scheduling_utils import get_weekday_target_dates, solve_weekday_schedule
from components.display_utils import build_display_name_map

DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金"}

HOURS = list(range(25))  # 0〜24
MINUTES = [0, 30]


def _time_select(label: str, default: str, key_prefix: str):
    """X時Y分のプルダウンで時間を入力し "HH:MM" を返す"""
    h_default, m_default = 9, 0
    if default:
        try:
            parts = default.split(":")
            h_default = int(parts[0])
            m_default = int(parts[1]) if len(parts) > 1 else 0
            if m_default not in MINUTES:
                m_default = 0
        except (ValueError, IndexError):
            pass
    c1, c2 = st.columns(2)
    with c1:
        h = st.selectbox(f"{label}（時）", HOURS, index=h_default,
                         key=f"{key_prefix}_h", label_visibility="collapsed")
    with c2:
        m = st.selectbox(f"{label}（分）", MINUTES,
                         index=MINUTES.index(m_default),
                         key=f"{key_prefix}_m", label_visibility="collapsed")
    return f"{h:02d}:{m:02d}"


def render(section: str):
    """副管理者の平日外勤管理画面"""
    cfg = get_weekday_config_by_section(section)
    if not cfg:
        st.error("セクション情報が見つかりません")
        return

    clinic_name = cfg["clinic_name"]
    days_of_week = cfg.get("days_of_week", [])
    assigned_doctor_ids = cfg.get("assigned_doctors", [])

    # ヘッダー
    col_title, col_logout = st.columns([5, 1])
    with col_title:
        days_str = "・".join(DAY_NAMES.get(d, str(d)) for d in days_of_week)
        st.markdown(f"**{clinic_name} 管理メニュー**　({days_str}曜日)")
    with col_logout:
        if st.button("ログアウト", use_container_width=True):
            st.session_state.role = None
            st.session_state.admin_authenticated = False
            st.session_state.admin_type = None
            st.session_state.doctor_section = None
            st.rerun()

    # 公開設定
    is_open = get_weekday_open_section(section)
    deadline = get_weekday_deadline(section)
    open_label = "公開中" if is_open else "未公開"
    deadline_label = f"（期限: {deadline}）" if deadline else ""

    col_info, col_open, col_dl = st.columns([3, 2, 2])
    with col_info:
        st.caption(f"希望入力: {open_label}{deadline_label}")
    with col_open:
        btn_label = "希望入力を閉じる" if is_open else "希望入力を公開"
        if st.button(btn_label, use_container_width=True,
                     type="secondary" if is_open else "primary"):
            set_weekday_open_section(section, not is_open)
            st.rerun()
    with col_dl:
        today = date.today()
        default_dl = (
            date.fromisoformat(deadline) if deadline
            else today + timedelta(days=7)
        )
        st.date_input(
            "入力期限", value=default_dl,
            key=f"wkadm_deadline_{section}",
            label_visibility="collapsed",
            on_change=lambda: set_weekday_deadline(
                section,
                st.session_state[f"wkadm_deadline_{section}"].isoformat(),
            ),
        )

    st.markdown("---")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "メンバー管理", "対象日管理", "スロット管理", "日別設定", "希望状況一覧", "スケジュール作成",
    ])

    with tab1:
        _render_members(section, cfg, assigned_doctor_ids)
    with tab2:
        _render_target_dates(section, days_of_week)
    with tab3:
        _render_slots(section, days_of_week)
    with tab4:
        _render_slot_overrides(section, days_of_week)
    with tab5:
        _render_preferences(section, assigned_doctor_ids)
    with tab6:
        _render_schedule(section, cfg, assigned_doctor_ids, days_of_week)


def _render_members(section: str, cfg: dict, assigned_doctor_ids: list):
    """メンバー管理タブ"""
    st.subheader("所属メンバー")

    all_doctors = get_doctors()
    doc_map = build_display_name_map(all_doctors)
    doc_ids = [d["id"] for d in all_doctors]

    current = [did for did in assigned_doctor_ids if did in doc_ids]
    current_names = ", ".join(doc_map.get(did, "?") for did in current) if current else "なし"
    st.write(f"現在のメンバー: {current_names}")

    with st.form(f"wkadm_members_{section}"):
        new_assigned = st.multiselect(
            "所属メンバー",
            options=doc_ids,
            default=current,
            format_func=lambda x: doc_map.get(x, "?"),
            key=f"wkadm_member_select_{section}",
        )
        if st.form_submit_button("メンバーを保存", type="primary"):
            update_weekday_config(section, assigned_doctors=new_assigned)
            st.success("メンバーを保存しました")
            st.rerun()


def _render_target_dates(section: str, days_of_week: list):
    """対象日管理タブ（週単位ON/OFF）"""
    st.subheader("対象日管理")
    st.caption("スケジュール対象となる日付を週単位で管理します")

    today = date.today()
    # 14ヶ月先まで生成
    all_dates = []
    for m_offset in range(14):
        dt = today + relativedelta(months=m_offset)
        month_dates = get_weekday_target_dates(dt.year, dt.month, days_of_week)
        all_dates.extend(month_dates)
    all_dates.sort()

    if not all_dates:
        st.info("対象となる日付がありません")
        return

    # 既存の対象日データ
    existing = db_get_target_dates(section)
    existing_map = {r["date"]: r["is_active"] for r in existing}

    # 週単位でグループ化
    weeks = {}
    for dt in all_dates:
        week_key = dt.isocalendar()[:2]  # (year, week_number)
        monday = dt - timedelta(days=dt.weekday())
        if week_key not in weeks:
            weeks[week_key] = {"monday": monday, "dates": []}
        weeks[week_key]["dates"].append(dt)

    # 月ごとにグループ化
    months_weeks = {}
    for week_key in sorted(weeks.keys()):
        week_info = weeks[week_key]
        # 週の最初の対象日の月で分類
        first_date = week_info["dates"][0]
        month_key = first_date.strftime("%Y-%m")
        if month_key not in months_weeks:
            months_weeks[month_key] = []
        months_weeks[month_key].append((week_key, week_info))

    # ---- 一括操作ボタン ----
    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("全選択", key=f"td_select_all_{section}", use_container_width=True):
            for week_key in weeks:
                st.session_state[f"wk_week_{section}_{week_key[0]}_{week_key[1]}"] = True
            st.rerun()
    with bc2:
        if st.button("全選択解除", key=f"td_deselect_all_{section}", use_container_width=True):
            for week_key in weeks:
                st.session_state[f"wk_week_{section}_{week_key[0]}_{week_key[1]}"] = False
            st.rerun()

    # ---- 月ごとに表示 ----
    changes = {}
    for month_key in sorted(months_weeks.keys()):
        month_week_list = months_weeks[month_key]
        # 月内の全週がアクティブか判定
        all_week_keys_in_month = [wk for wk, _ in month_week_list]
        month_all_active = all(
            all(existing_map.get(d.isoformat(), 1) for d in weeks[wk]["dates"])
            for wk in all_week_keys_in_month
        )

        try:
            y, m = map(int, month_key.split("-"))
            month_label = f"{y}年{m}月"
        except ValueError:
            month_label = month_key

        with st.expander(month_label, expanded=False):
            # 月一括選択ボタン
            mc1, mc2 = st.columns(2)
            with mc1:
                if st.button("この月を全選択", key=f"td_mon_sel_{section}_{month_key}",
                             use_container_width=True):
                    for wk in all_week_keys_in_month:
                        st.session_state[f"wk_week_{section}_{wk[0]}_{wk[1]}"] = True
                    st.rerun()
            with mc2:
                if st.button("この月を全解除", key=f"td_mon_desel_{section}_{month_key}",
                             use_container_width=True):
                    for wk in all_week_keys_in_month:
                        st.session_state[f"wk_week_{section}_{wk[0]}_{wk[1]}"] = False
                    st.rerun()

            # 週ごとのチェックボックス
            for week_key, week_info in month_week_list:
                monday = week_info["monday"]
                dates = week_info["dates"]
                dates_str = ", ".join(d.strftime("%m/%d(%a)") for d in dates)
                week_label = f"{monday.strftime('%Y-%m-%d')}週 ({dates_str})"

                date_strs = [d.isoformat() for d in dates]
                current_active = all(existing_map.get(ds, 1) for ds in date_strs)

                cb_key = f"wk_week_{section}_{week_key[0]}_{week_key[1]}"
                # 一括ボタンで既にセッション状態が設定済みなら value を渡さない
                if cb_key in st.session_state:
                    is_on = st.checkbox(week_label, key=cb_key)
                else:
                    is_on = st.checkbox(week_label, value=current_active, key=cb_key)

                for ds in date_strs:
                    if is_on != bool(existing_map.get(ds, 1)):
                        changes[ds] = is_on

    if st.button("対象日を保存", type="primary", key=f"save_target_dates_{section}"):
        if changes:
            all_date_strs = [d.isoformat() for d in all_dates]
            active_dates = []
            for ds in all_date_strs:
                if ds in changes:
                    if changes[ds]:
                        active_dates.append(ds)
                elif existing_map.get(ds, 1):
                    active_dates.append(ds)
            set_target_dates(section, all_date_strs, active_dates)
            st.success(f"対象日を保存しました（{len(changes)}件変更）")
        else:
            if not existing:
                all_date_strs = [d.isoformat() for d in all_dates]
                set_target_dates(section, all_date_strs, all_date_strs)
                st.success("対象日を初期化しました")
            else:
                st.info("変更はありません")
        st.rerun()


def _render_slots(section: str, days_of_week: list):
    """スロット管理タブ"""
    st.subheader("スロット管理")
    st.caption("各曜日の時間枠を定義します")

    # スロット追加
    with st.expander("スロットの追加", expanded=False):
        with st.form(f"add_slot_{section}", clear_on_submit=True):
            slot_name = st.text_input("スロット名", placeholder="例: 午前外来")
            day_of_week = st.selectbox(
                "曜日",
                options=days_of_week,
                format_func=lambda x: DAY_NAMES.get(x, str(x)),
            )
            st.caption("開始時間")
            start_time = _time_select("開始", "09:00", f"add_slot_start_{section}")
            st.caption("終了時間")
            end_time = _time_select("終了", "17:00", f"add_slot_end_{section}")
            req_count = st.number_input("必要人数", min_value=1, max_value=10, value=1)
            if st.form_submit_button("追加", use_container_width=True):
                if not slot_name.strip():
                    st.error("スロット名を入力してください")
                else:
                    add_weekday_slot(section, slot_name.strip(), day_of_week,
                                     start_time, end_time, req_count)
                    st.success(f"スロット「{slot_name}」を追加しました")
                    st.rerun()

    # 既存スロット一覧
    slots = get_weekday_slots(section)
    if not slots:
        st.info("スロットが登録されていません")
        return

    # 曜日ごとにグループ表示
    for dow in sorted(days_of_week):
        dow_slots = [s for s in slots if s["day_of_week"] == dow]
        if not dow_slots:
            continue
        st.write(f"**{DAY_NAMES.get(dow, str(dow))}曜日**")
        for s in dow_slots:
            status = "有効" if s["is_active"] else "無効"
            with st.container(border=True):
                st.markdown(
                    f"**{s['slot_name']}**　{s['start_time']}〜{s['end_time']}　"
                    f"必要人数: {s['required_count']}　{status}"
                )
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    if s["is_active"]:
                        if st.button("無効化", key=f"slot_deact_{s['id']}", use_container_width=True):
                            update_weekday_slot(s["id"], is_active=0)
                            st.rerun()
                    else:
                        if st.button("有効化", key=f"slot_act_{s['id']}", use_container_width=True):
                            update_weekday_slot(s["id"], is_active=1)
                            st.rerun()
                with bc2:
                    if st.button("編集", key=f"slot_edit_{s['id']}", use_container_width=True):
                        st.session_state[f"slot_editing_{s['id']}"] = True
                with bc3:
                    if st.button("削除", key=f"slot_del_{s['id']}", type="secondary", use_container_width=True):
                        st.session_state[f"slot_confirm_del_{s['id']}"] = True

            if st.session_state.get(f"slot_editing_{s['id']}"):
                with st.form(f"slot_edit_form_{s['id']}"):
                    e_name = st.text_input("スロット名", value=s["slot_name"], key=f"se_name_{s['id']}")
                    st.caption("開始時間")
                    e_start = _time_select("開始", s["start_time"], f"se_start_{s['id']}")
                    st.caption("終了時間")
                    e_end = _time_select("終了", s["end_time"], f"se_end_{s['id']}")
                    e_req = st.number_input("必要人数", min_value=1, max_value=10,
                                            value=s["required_count"], key=f"se_req_{s['id']}")
                    fc1, fc2 = st.columns(2)
                    with fc1:
                        if st.form_submit_button("保存"):
                            update_weekday_slot(s["id"], slot_name=e_name,
                                                start_time=e_start, end_time=e_end,
                                                required_count=e_req)
                            st.session_state.pop(f"slot_editing_{s['id']}", None)
                            st.success("保存しました")
                            st.rerun()
                    with fc2:
                        if st.form_submit_button("キャンセル"):
                            st.session_state.pop(f"slot_editing_{s['id']}", None)
                            st.rerun()

            if st.session_state.get(f"slot_confirm_del_{s['id']}"):
                st.warning(f"スロット「{s['slot_name']}」を削除しますか？")
                dc1, dc2 = st.columns(2)
                with dc1:
                    if st.button("削除する", key=f"slot_do_del_{s['id']}", type="primary"):
                        delete_weekday_slot(s["id"])
                        st.session_state.pop(f"slot_confirm_del_{s['id']}", None)
                        st.success("削除しました")
                        st.rerun()
                with dc2:
                    if st.button("キャンセル", key=f"slot_cancel_del_{s['id']}"):
                        st.session_state.pop(f"slot_confirm_del_{s['id']}", None)
                        st.rerun()


def _render_slot_overrides(section: str, days_of_week: list):
    """日別設定タブ（休診・2人体制のオーバーライド）"""
    st.subheader("日別設定")
    st.caption("特定の日に休診にしたり、2人体制にしたりできます")

    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(14)]
    ovr_month = st.selectbox("対象月", months, key=f"wkadm_ovr_month_{section}")

    slots = get_weekday_slots(section)
    active_slots = [s for s in slots if s.get("is_active", 1)]
    if not active_slots:
        st.info("スロットが登録されていません。「スロット管理」タブで設定してください。")
        return

    active_dates_str = get_active_target_dates(section)
    year_m, month_m = map(int, ovr_month.split("-"))
    target_dates = []
    for ds in active_dates_str:
        try:
            dt = date.fromisoformat(ds)
            if dt.year == year_m and dt.month == month_m:
                target_dates.append(dt)
        except ValueError:
            pass
    target_dates.sort()

    if not target_dates:
        st.info("この月の対象日がありません。")
        return

    overrides = get_weekday_slot_overrides(section, ovr_month)

    OVERRIDE_OPTIONS = ["通常", "2人体制", "休診"]
    REQ_MAP = {"通常": -1, "2人体制": 2, "休診": 0}  # -1 = デフォルト(オーバーライドなし)
    REQ_TO_LABEL = {0: "休診", 2: "2人体制"}

    # スロットごとに表示
    changes = {}
    for slot in active_slots:
        dow_slots_dates = [dt for dt in target_dates if dt.weekday() == slot["day_of_week"]]
        if not dow_slots_dates:
            continue

        st.write(f"**{slot['slot_name']}** ({DAY_NAMES.get(slot['day_of_week'], '')}曜　"
                 f"{slot['start_time']}〜{slot['end_time']}　通常: {slot['required_count']}人)")

        cols = st.columns(min(len(dow_slots_dates), 5))
        for i, dt in enumerate(dow_slots_dates):
            ds = dt.isoformat()
            current_ovr = overrides.get((slot["id"], ds))
            if current_ovr is not None:
                current_label = REQ_TO_LABEL.get(current_ovr, "通常")
            else:
                current_label = "通常"

            with cols[i % len(cols)]:
                sel = st.radio(
                    dt.strftime("%m/%d(%a)"),
                    OVERRIDE_OPTIONS,
                    index=OVERRIDE_OPTIONS.index(current_label),
                    key=f"ovr_{section}_{slot['id']}_{ds}",
                )
                new_req = REQ_MAP[sel]
                if new_req == -1:
                    # 通常 → オーバーライドが既存なら削除相当（デフォルトに戻す）
                    if current_ovr is not None:
                        changes[(slot["id"], ds)] = slot["required_count"]
                elif current_ovr is None or new_req != current_ovr:
                    changes[(slot["id"], ds)] = new_req

    if st.button("日別設定を保存", type="primary", key=f"save_ovr_{section}"):
        if changes:
            set_weekday_slot_overrides_batch(section, changes)
            st.success(f"日別設定を保存しました（{len(changes)}件変更）")
        else:
            st.info("変更はありません")
        st.rerun()


def _render_preferences(section: str, assigned_doctor_ids: list):
    """希望状況一覧タブ"""
    st.subheader("希望状況一覧")

    all_doctors = get_doctors(active_only=False)
    doc_map = {d["id"]: d["name"] for d in all_doctors}

    prefs = get_weekday_preferences(section)
    pref_map = {p["doctor_id"]: p for p in prefs}

    active_dates = get_active_target_dates(section)
    if not active_dates:
        st.info("対象日が設定されていません。「対象日管理」タブで設定してください。")
        return

    # 各医員の希望状況を表示
    for doc_id in assigned_doctor_ids:
        name = doc_map.get(doc_id, f"ID:{doc_id}")
        pref = pref_map.get(doc_id)
        if pref:
            ng = pref.get("ng_dates") or []
            avoid = pref.get("avoid_dates") or []
            free = pref.get("free_text", "")
            ng_in_range = [d for d in ng if d in active_dates]
            avoid_in_range = [d for d in avoid if d in active_dates]
            status = f"NG: {len(ng_in_range)}日, △: {len(avoid_in_range)}日"
            if free:
                status += f", 備考あり"
            st.write(f"**{name}**: {status}")
            if ng_in_range:
                st.caption(f"　NG: {', '.join(ng_in_range)}")
            if avoid_in_range:
                st.caption(f"　△: {', '.join(avoid_in_range)}")
            if free:
                st.caption(f"　備考: {free}")
        else:
            st.write(f"**{name}**: 未入力")

    # ---- 代行入力 ----
    st.markdown("---")
    st.subheader("代行入力")
    st.caption("医員に代わって希望を入力できます")

    proxy_doc_options = [d for d in assigned_doctor_ids]
    if not proxy_doc_options:
        st.info("メンバーが登録されていません")
        return

    proxy_doc_id = st.selectbox(
        "対象医員",
        proxy_doc_options,
        format_func=lambda x: doc_map.get(x, f"ID:{x}"),
        key=f"proxy_doc_{section}",
    )

    if proxy_doc_id:
        _render_proxy_preference_form(proxy_doc_id, section, active_dates, pref_map, doc_map)


def _render_proxy_preference_form(doc_id: int, section: str,
                                   active_dates: list, pref_map: dict,
                                   doc_map: dict):
    """代行希望入力フォーム"""
    pref = pref_map.get(doc_id)
    existing_ng = set(pref.get("ng_dates", []) if pref else [])
    existing_avoid = set(pref.get("avoid_dates", []) if pref else [])
    existing_free = pref.get("free_text", "") if pref else ""

    SCHEDULE_STATUS = ["○", "△", "×"]

    with st.form(f"proxy_pref_{section}_{doc_id}"):
        st.write(f"**{doc_map.get(doc_id, '')}** の希望を入力（○=可能　△=できれば避けたい　×=NG）")
        n_cols = min(len(active_dates), 5)
        cols = st.columns(n_cols)

        for i, ds in enumerate(active_dates):
            try:
                dt = date.fromisoformat(ds)
                label = dt.strftime("%m/%d(%a)")
            except ValueError:
                label = ds

            if ds in existing_ng:
                default_idx = 2
            elif ds in existing_avoid:
                default_idx = 1
            else:
                default_idx = 0

            with cols[i % n_cols]:
                st.selectbox(
                    label,
                    options=SCHEDULE_STATUS,
                    index=default_idx,
                    key=f"proxy_{section}_{doc_id}_{ds}",
                )

        free_text = st.text_area(
            "備考",
            value=existing_free,
            placeholder="例: 第3週は学会のため不可",
            key=f"proxy_free_{section}_{doc_id}",
        )

        if st.form_submit_button("希望を保存（代行）", type="primary"):
            new_ng = []
            new_avoid = []
            for ds in active_dates:
                val = st.session_state.get(f"proxy_{section}_{doc_id}_{ds}", "○")
                if val == "×":
                    new_ng.append(ds)
                elif val == "△":
                    new_avoid.append(ds)

            upsert_weekday_preference(
                doc_id, section,
                ng_dates=new_ng,
                avoid_dates=new_avoid,
                free_text=free_text,
            )
            st.success(f"{doc_map.get(doc_id, '')} の希望を保存しました")
            st.rerun()


def _render_schedule(section: str, cfg: dict, assigned_doctor_ids: list, days_of_week: list):
    """スケジュール作成タブ"""
    st.subheader("スケジュール作成")

    # 対象月選択
    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(14)]
    target_month = st.selectbox("対象月", months, key=f"wkadm_month_{section}")

    active_dates_str = get_active_target_dates(section)
    year_m, month_m = map(int, target_month.split("-"))

    # 対象月内の有効日付のみフィルタ
    target_dates = []
    for ds in active_dates_str:
        try:
            dt = date.fromisoformat(ds)
            if dt.year == year_m and dt.month == month_m:
                target_dates.append(dt)
        except ValueError:
            pass
    target_dates.sort()

    if not target_dates:
        st.info("この月の対象日がありません。「対象日管理」タブで設定してください。")
        return

    slots = get_weekday_slots(section)
    active_slots = [s for s in slots if s.get("is_active", 1)]
    if not active_slots:
        st.info("スロットが登録されていません。「スロット管理」タブで設定してください。")
        return

    all_doctors = get_doctors()
    doc_map = build_display_name_map(all_doctors)
    assigned_doctors = [d for d in all_doctors if d["id"] in assigned_doctor_ids]

    if not assigned_doctors:
        st.info("メンバーが登録されていません。「メンバー管理」タブで設定してください。")
        return

    # 既存スケジュール読み込み
    existing_sched = get_weekday_schedule(target_month, section)
    existing_map = {}  # {date_str: {slot_id: [doctor_id, ...]}}
    for r in existing_sched:
        ds = r["date"]
        sid = r["slot_id"]
        if ds not in existing_map:
            existing_map[ds] = {}
        if sid not in existing_map[ds]:
            existing_map[ds][sid] = []
        existing_map[ds][sid].append(r["doctor_id"])

    prefs = get_weekday_preferences(section)

    st.write(f"対象日: {len(target_dates)}日　メンバー: {len(assigned_doctors)}名　スロット: {len(active_slots)}枠")

    # オーバーライド取得
    slot_overrides = get_weekday_slot_overrides(section, target_month)

    # 自動生成ボタン
    if st.button("自動生成", type="primary", key=f"auto_gen_{section}"):
        result = solve_weekday_schedule(target_dates, active_slots, assigned_doctors, prefs,
                                        slot_overrides=slot_overrides)
        if result is None:
            st.error("条件を満たすスケジュールが見つかりませんでした。制約を確認してください。")
        else:
            batch_save_weekday_assignments(target_month, section, result)
            st.success("スケジュールを自動生成しました")
            st.rerun()

    st.markdown("---")

    # 手動編集マトリクス
    st.write("**スケジュール編集**")
    doc_ids = [d["id"] for d in assigned_doctors]
    doc_options = [0] + doc_ids  # 0 = 未割当

    def _doc_label(did):
        if did == 0:
            return "---"
        return doc_map.get(did, str(did))

    # 日付×スロットのグリッド
    for dt in target_dates:
        ds = dt.isoformat()
        dow = dt.weekday()
        day_slots = [s for s in active_slots if s["day_of_week"] == dow]
        if not day_slots:
            continue

        st.write(f"**{dt.strftime('%m/%d(%a)')}**")
        cols = st.columns(len(day_slots))
        for j, slot in enumerate(day_slots):
            with cols[j]:
                # オーバーライド確認
                ovr_req = slot_overrides.get((slot["id"], ds))
                if ovr_req is not None and ovr_req == 0:
                    st.caption(f"{slot['slot_name']}: 休診")
                    continue
                req_count = ovr_req if ovr_req is not None else slot["required_count"]
                if ovr_req is not None:
                    st.caption(f"({req_count}人体制)")

                current_assigned = existing_map.get(ds, {}).get(slot["id"], [])
                for k in range(req_count):
                    current_val = current_assigned[k] if k < len(current_assigned) else 0
                    default_idx = doc_options.index(current_val) if current_val in doc_options else 0
                    st.selectbox(
                        f"{slot['slot_name']} #{k+1}",
                        options=doc_options,
                        index=default_idx,
                        format_func=_doc_label,
                        key=f"sched_{section}_{ds}_{slot['id']}_{k}",
                    )

    if st.button("スケジュールを保存", type="primary", key=f"save_sched_{section}"):
        assignments = {}
        for dt in target_dates:
            ds = dt.isoformat()
            dow = dt.weekday()
            day_slots = [s for s in active_slots if s["day_of_week"] == dow]
            if not day_slots:
                continue
            assignments[ds] = {}
            for slot in day_slots:
                ovr_req = slot_overrides.get((slot["id"], ds))
                if ovr_req is not None and ovr_req == 0:
                    continue  # 休診
                req_count = ovr_req if ovr_req is not None else slot["required_count"]
                assigned = []
                for k in range(req_count):
                    val = st.session_state.get(f"sched_{section}_{ds}_{slot['id']}_{k}", 0)
                    if val and val != 0:
                        assigned.append(val)
                assignments[ds][slot["id"]] = assigned

        batch_save_weekday_assignments(target_month, section, assignments)
        st.success("スケジュールを保存しました")
        st.rerun()
