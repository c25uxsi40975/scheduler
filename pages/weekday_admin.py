"""
副管理者UI（平日外勤管理）
セクションパラメータで各医院共通のUIを提供
主管理者も admin_type でセクション指定してアクセス可能
"""
import json
import requests
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
    """代行希望入力フォーム（テーブル形式: 行=日付, 列=スロット）"""
    import pandas as pd

    pref = pref_map.get(doc_id)
    existing_ng = set(pref.get("ng_dates", []) if pref else [])
    existing_avoid = set(pref.get("avoid_dates", []) if pref else [])
    existing_free = pref.get("free_text", "") if pref else ""

    SCHEDULE_STATUS = ["○", "△", "×"]

    # スロット情報を取得
    slots = get_weekday_slots(section)
    active_slots = [s for s in slots if s.get("is_active", 1)]

    # 曜日→スロットのマップ
    dow_slots = {}
    for s in active_slots:
        dow = s["day_of_week"]
        if dow not in dow_slots:
            dow_slots[dow] = []
        dow_slots[dow].append(s)

    # 全スロット名（列ヘッダー用）— 曜日横断でユニーク名を収集
    all_slot_names = []
    seen = set()
    for dow in sorted(dow_slots.keys()):
        for s in dow_slots[dow]:
            if s["slot_name"] not in seen:
                all_slot_names.append(s["slot_name"])
                seen.add(s["slot_name"])

    st.write(f"**{doc_map.get(doc_id, '')}** の希望（○=可能　△=できれば避けたい　×=NG）")

    if not all_slot_names:
        st.info("スロットが登録されていません")
        return

    # テーブルデータ構築用: 各行のどのスロットが有効かを記録
    date_slot_valid = []  # [{slot_name: bool}]

    # セッション状態のテーブルキー
    table_key = f"proxy_table_{section}_{doc_id}"

    # テーブルデータ構築
    rows = []
    date_keys = []
    for ds in active_dates:
        try:
            dt = date.fromisoformat(ds)
            date_label = dt.strftime("%m/%d(%a)")
            dow = dt.weekday()
        except ValueError:
            continue

        if ds in existing_ng:
            status = "×"
        elif ds in existing_avoid:
            status = "△"
        else:
            status = "○"

        day_slot_names = {s["slot_name"] for s in dow_slots.get(dow, [])}
        validity = {}

        row = {"日付": date_label}
        for sn in all_slot_names:
            if sn in day_slot_names:
                row[sn] = status
                validity[sn] = True
            else:
                row[sn] = "-"
                validity[sn] = False
        rows.append(row)
        date_keys.append(ds)
        date_slot_valid.append(validity)

    if not rows:
        st.info("対象日がありません")
        return

    # 一括操作用の別キー（data_editorのwidgetキーと分離）
    bulk_key = f"proxy_bulk_{section}_{doc_id}"

    # ---- 一括操作ボタン ----
    st.caption("一括操作")
    # 全体
    gc1, gc2, gc3 = st.columns(3)
    with gc1:
        if st.button("全て ○", key=f"proxy_all_ok_{section}_{doc_id}", use_container_width=True):
            st.session_state[bulk_key] = {
                i: {sn: "○" for sn in all_slot_names if date_slot_valid[i].get(sn)}
                for i in range(len(rows))
            }
            st.rerun()
    with gc2:
        if st.button("全て ×", key=f"proxy_all_ng_{section}_{doc_id}", use_container_width=True):
            st.session_state[bulk_key] = {
                i: {sn: "×" for sn in all_slot_names if date_slot_valid[i].get(sn)}
                for i in range(len(rows))
            }
            st.rerun()
    with gc3:
        if st.button("全て △", key=f"proxy_all_avoid_{section}_{doc_id}", use_container_width=True):
            st.session_state[bulk_key] = {
                i: {sn: "△" for sn in all_slot_names if date_slot_valid[i].get(sn)}
                for i in range(len(rows))
            }
            st.rerun()

    # スロット単位の操作
    st.caption("スロット単位の一括操作")
    for sn in all_slot_names:
        sc1, sc2, sc3, sc4 = st.columns([2, 1, 1, 1])
        with sc1:
            st.markdown(f"**{sn}**")
        with sc2:
            if st.button("○", key=f"proxy_slot_ok_{section}_{doc_id}_{sn}", use_container_width=True):
                edits = {k: dict(v) for k, v in st.session_state.get(bulk_key, {}).items()}
                for i in range(len(rows)):
                    if date_slot_valid[i].get(sn):
                        edits.setdefault(i, {})[sn] = "○"
                st.session_state[bulk_key] = edits
                st.rerun()
        with sc3:
            if st.button("×", key=f"proxy_slot_ng_{section}_{doc_id}_{sn}", use_container_width=True):
                edits = {k: dict(v) for k, v in st.session_state.get(bulk_key, {}).items()}
                for i in range(len(rows)):
                    if date_slot_valid[i].get(sn):
                        edits.setdefault(i, {})[sn] = "×"
                st.session_state[bulk_key] = edits
                st.rerun()
        with sc4:
            if st.button("-", key=f"proxy_slot_off_{section}_{doc_id}_{sn}",
                         use_container_width=True, help="無効化"):
                edits = {k: dict(v) for k, v in st.session_state.get(bulk_key, {}).items()}
                for i in range(len(rows)):
                    if date_slot_valid[i].get(sn):
                        edits.setdefault(i, {})[sn] = "-"
                st.session_state[bulk_key] = edits
                st.rerun()

    st.markdown("---")

    # 一括操作の結果をDataFrameに反映（popせず保持して蓄積可能にする）
    bulk_edits = st.session_state.get(bulk_key)
    if bulk_edits:
        for i, col_vals in bulk_edits.items():
            for col, val in col_vals.items():
                rows[int(i)][col] = val

    df = pd.DataFrame(rows)

    column_config = {
        "日付": st.column_config.TextColumn("日付", disabled=True, width="small"),
    }
    for sn in all_slot_names:
        column_config[sn] = st.column_config.SelectboxColumn(
            sn,
            options=SCHEDULE_STATUS + ["-"],
            width="small",
        )

    edited_df = st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        use_container_width=True,
        key=table_key,
    )

    free_text = st.text_area(
        "備考",
        value=existing_free,
        placeholder="例: 第3週は学会のため不可",
        key=f"proxy_free_{section}_{doc_id}",
    )

    if st.button("希望を保存（代行）", type="primary", key=f"proxy_save_{section}_{doc_id}"):
        new_ng = []
        new_avoid = []
        for i, ds in enumerate(date_keys):
            if i >= len(edited_df):
                break
            row = edited_df.iloc[i]
            statuses = [row[sn] for sn in all_slot_names if row.get(sn, "-") != "-"]
            if not statuses:
                continue
            if "×" in statuses:
                new_ng.append(ds)
            elif "△" in statuses:
                new_avoid.append(ds)

        upsert_weekday_preference(
            doc_id, section,
            ng_dates=new_ng,
            avoid_dates=new_avoid,
            free_text=free_text,
        )
        st.session_state.pop(bulk_key, None)
        st.success(f"{doc_map.get(doc_id, '')} の希望を保存しました")
        st.rerun()


def _render_schedule(section: str, cfg: dict, assigned_doctor_ids: list, days_of_week: list):
    """スケジュール作成タブ"""
    st.subheader("スケジュール作成")

    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(14)]

    # 生成モード選択
    gen_mode = st.radio(
        "生成範囲",
        ["月単位", "期間指定（複数月を均一化）"],
        horizontal=True,
        key=f"wkadm_gen_mode_{section}",
    )

    if gen_mode == "月単位":
        target_month = st.selectbox("対象月", months, key=f"wkadm_month_{section}")
        selected_months = [target_month]
    else:
        mc1, mc2 = st.columns(2)
        with mc1:
            start_month = st.selectbox("開始月", months, key=f"wkadm_start_month_{section}")
        with mc2:
            start_idx = months.index(start_month) if start_month in months else 0
            end_options = months[start_idx:]
            end_month = st.selectbox("終了月", end_options, key=f"wkadm_end_month_{section}")
        si = months.index(start_month)
        ei = months.index(end_month)
        selected_months = months[si:ei + 1]

    active_dates_str = get_active_target_dates(section)

    # 選択期間の全対象日をフィルタ
    target_dates = []
    for ds in active_dates_str:
        try:
            dt = date.fromisoformat(ds)
            ym = dt.strftime("%Y-%m")
            if ym in selected_months:
                target_dates.append(dt)
        except ValueError:
            pass
    target_dates.sort()

    if not target_dates:
        st.info("この期間の対象日がありません。「対象日管理」タブで設定してください。")
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

    prefs = get_weekday_preferences(section)

    # 全選択月のオーバーライドを統合
    all_slot_overrides = {}
    for ym in selected_months:
        ovr = get_weekday_slot_overrides(section, ym)
        all_slot_overrides.update(ovr)

    # 既存スケジュール読み込み（全選択月分）
    all_existing_sched = []
    for ym in selected_months:
        all_existing_sched.extend(get_weekday_schedule(ym, section))
    existing_map = {}
    for r in all_existing_sched:
        ds = r["date"]
        sid = r["slot_id"]
        existing_map.setdefault(ds, {}).setdefault(sid, []).append(r["doctor_id"])

    period_label = selected_months[0] if len(selected_months) == 1 else \
        f"{selected_months[0]}〜{selected_months[-1]}"
    st.write(f"対象期間: {period_label}　対象日: {len(target_dates)}日　"
             f"メンバー: {len(assigned_doctors)}名　スロット: {len(active_slots)}枠")

    # ---- アサイン状況サマリ（既存スケジュール） ----
    _render_assignment_summary(existing_map, assigned_doctors, doc_map, active_slots,
                               target_dates, all_slot_overrides)

    preview_key = f"wkadm_preview_{section}"

    # 自動生成ボタン
    if st.button("スケジュール案を生成", type="primary", key=f"auto_gen_{section}"):
        result = solve_weekday_schedule(target_dates, active_slots, assigned_doctors, prefs,
                                        slot_overrides=all_slot_overrides)
        if result is None:
            st.error("条件を満たすスケジュールが見つかりませんでした。制約を確認してください。")
        else:
            st.session_state[preview_key] = result
            st.session_state["_toast_msg"] = "スケジュール案を生成しました。内容を確認して確定してください。"
            st.rerun()

    # ---- プレビュー表示 ----
    preview_result = st.session_state.get(preview_key)
    if preview_result:
        st.markdown("---")
        st.subheader("生成プレビュー")
        st.info("内容を確認し、問題なければ「確定して保存」を押してください。")

        # プレビューのアサイン状況サマリ
        _render_assignment_summary(preview_result, assigned_doctors, doc_map, active_slots,
                                   target_dates, all_slot_overrides)

        # NG日・避けたい日の警告
        _render_preview_warnings(preview_result, assigned_doctors, doc_map, prefs)

        # プレビューテーブル表示
        _render_preview_table(preview_result, target_dates, active_slots,
                              all_slot_overrides, doc_map)

        # 確定 / 破棄ボタン
        btn_cols = st.columns(2)
        with btn_cols[0]:
            if st.button("確定して保存", type="primary", key=f"confirm_preview_{section}"):
                for ym in selected_months:
                    month_result = {ds: slots_map for ds, slots_map in preview_result.items()
                                   if ds.startswith(ym)}
                    if month_result:
                        batch_save_weekday_assignments(ym, section, month_result)
                # 確定通知
                gas_url = st.secrets.get("gas_webapp_url", "")
                if gas_url:
                    try:
                        requests.post(gas_url, json={
                            "action": "weekday_schedule_confirmed",
                            "section": section,
                            "clinic_name": cfg["clinic_name"],
                            "year_months": selected_months,
                        }, timeout=10)
                    except requests.RequestException:
                        pass
                del st.session_state[preview_key]
                st.session_state["_toast_msg"] = "スケジュールを確定しました"
                st.rerun()
        with btn_cols[1]:
            if st.button("破棄", key=f"discard_preview_{section}"):
                del st.session_state[preview_key]
                st.rerun()

    st.markdown("---")

    # ---- 月ごとの手動編集（既存スケジュール） ----
    st.write("**スケジュール編集**")
    doc_ids = [d["id"] for d in assigned_doctors]
    doc_options = [0] + doc_ids

    def _doc_label(did):
        if did == 0:
            return "---"
        return doc_map.get(did, str(did))

    # 月ごとにexpanderで表示
    for ym in selected_months:
        month_dates = [dt for dt in target_dates if dt.strftime("%Y-%m") == ym]
        if not month_dates:
            continue

        with st.expander(f"📅 {ym}", expanded=(len(selected_months) == 1)):
            for dt in month_dates:
                ds = dt.isoformat()
                dow = dt.weekday()
                day_slots = [s for s in active_slots if s["day_of_week"] == dow]
                if not day_slots:
                    continue

                st.write(f"**{dt.strftime('%m/%d(%a)')}**")
                cols = st.columns(len(day_slots))
                for j, slot in enumerate(day_slots):
                    with cols[j]:
                        ovr_req = all_slot_overrides.get((slot["id"], ds))
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
        for ym in selected_months:
            month_dates = [dt for dt in target_dates if dt.strftime("%Y-%m") == ym]
            assignments = {}
            for dt in month_dates:
                ds = dt.isoformat()
                dow = dt.weekday()
                day_slots = [s for s in active_slots if s["day_of_week"] == dow]
                if not day_slots:
                    continue
                assignments[ds] = {}
                for slot in day_slots:
                    ovr_req = all_slot_overrides.get((slot["id"], ds))
                    if ovr_req is not None and ovr_req == 0:
                        continue
                    req_count = ovr_req if ovr_req is not None else slot["required_count"]
                    assigned = []
                    for k in range(req_count):
                        val = st.session_state.get(f"sched_{section}_{ds}_{slot['id']}_{k}", 0)
                        if val and val != 0:
                            assigned.append(val)
                    assignments[ds][slot["id"]] = assigned
            if assignments:
                batch_save_weekday_assignments(ym, section, assignments)
        st.success("スケジュールを保存しました")
        st.rerun()


def _render_assignment_summary(existing_map: dict, assigned_doctors: list,
                                doc_map: dict, active_slots: list,
                                target_dates: list, slot_overrides: dict):
    """医員ごとのアサイン回数サマリを表示"""
    # 各医員のアサイン回数をカウント
    count_map = {d["id"]: 0 for d in assigned_doctors}
    for ds, slots_map in existing_map.items():
        for sid, doc_ids in slots_map.items():
            for did in doc_ids:
                if did in count_map:
                    count_map[did] += 1

    # 合計必要スロット数
    total_needed = 0
    for dt in target_dates:
        dow = dt.weekday()
        for s in active_slots:
            if s["day_of_week"] == dow:
                ovr = slot_overrides.get((s["id"], dt.isoformat()))
                if ovr is not None:
                    total_needed += max(ovr, 0)
                else:
                    total_needed += s["required_count"]

    total_assigned = sum(count_map.values())
    n_docs = len(assigned_doctors)
    avg = total_needed / n_docs if n_docs > 0 else 0

    with st.expander("📊 アサイン状況", expanded=True):
        st.caption(f"必要総枠: {total_needed}　割当済: {total_assigned}　"
                   f"平均: {avg:.1f}回/人")

        # 医員ごとの表示
        sorted_docs = sorted(assigned_doctors, key=lambda d: -count_map.get(d["id"], 0))
        for d in sorted_docs:
            cnt = count_map.get(d["id"], 0)
            name = doc_map.get(d["id"], str(d["id"]))
            diff = cnt - avg
            if abs(diff) < 0.5:
                bar_color = "🟢"
            elif diff > 0:
                bar_color = "🔴" if diff > 1.5 else "🟡"
            else:
                bar_color = "🔵" if diff < -1.5 else "🟡"
            st.write(f"{bar_color} **{name}**: {cnt}回　(平均比 {diff:+.1f})")


def _render_preview_warnings(preview_result: dict, assigned_doctors: list,
                              doc_map: dict, prefs: list):
    """プレビュー結果のNG日・避けたい日警告を表示"""
    from scheduling_utils import is_ng_date, is_avoid_date

    ng_hits = []
    avoid_hits = []
    for ds, slots_map in preview_result.items():
        for sid, doc_ids in slots_map.items():
            for did in doc_ids:
                name = doc_map.get(did, str(did))
                d_obj = date.fromisoformat(ds)
                label = f"{name} → {d_obj.strftime('%m/%d')}"
                if is_ng_date(did, ds, prefs):
                    ng_hits.append(label)
                if is_avoid_date(did, ds, prefs):
                    avoid_hits.append(label)

    if ng_hits:
        st.error(f"NG日に割り当てがあります（{len(ng_hits)}件）: " + "、".join(ng_hits))
    if avoid_hits:
        st.warning(f"△（できれば避けたい）日に割り当てがあります（{len(avoid_hits)}件）: "
                   + "、".join(avoid_hits))


def _render_preview_table(preview_result: dict, target_dates: list,
                           active_slots: list, slot_overrides: dict,
                           doc_map: dict):
    """プレビュー結果をテーブル形式で表示"""
    # 月ごとにグループ化
    months = sorted(set(dt.strftime("%Y-%m") for dt in target_dates))

    for ym in months:
        month_dates = [dt for dt in target_dates if dt.strftime("%Y-%m") == ym]
        if not month_dates:
            continue

        with st.expander(f"📅 {ym}", expanded=True):
            for dt in month_dates:
                ds = dt.isoformat()
                dow = dt.weekday()
                day_slots = [s for s in active_slots if s["day_of_week"] == dow]
                if not day_slots:
                    continue

                day_assignments = preview_result.get(ds, {})
                parts = []
                for slot in day_slots:
                    ovr_req = slot_overrides.get((slot["id"], ds))
                    if ovr_req is not None and ovr_req == 0:
                        parts.append(f"{slot['slot_name']}: 休診")
                        continue
                    assigned = day_assignments.get(slot["id"], [])
                    names = [doc_map.get(did, str(did)) for did in assigned]
                    parts.append(f"{slot['slot_name']}: {', '.join(names) if names else '---'}")

                st.write(f"**{dt.strftime('%m/%d(%a)')}** — " + "　|　".join(parts))
