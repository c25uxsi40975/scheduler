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
)
from scheduling_utils import get_weekday_target_dates, solve_weekday_schedule
from components.display_utils import build_display_name_map

DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}


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

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "メンバー管理", "対象日管理", "スロット管理", "希望状況一覧", "スケジュール作成",
    ])

    with tab1:
        _render_members(section, cfg, assigned_doctor_ids)
    with tab2:
        _render_target_dates(section, days_of_week)
    with tab3:
        _render_slots(section, days_of_week)
    with tab4:
        _render_preferences(section, assigned_doctor_ids)
    with tab5:
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
    # 3ヶ月先まで生成
    all_dates = []
    for m_offset in range(4):
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
        # ISO week
        week_key = dt.isocalendar()[:2]  # (year, week_number)
        monday = dt - timedelta(days=dt.weekday())
        if week_key not in weeks:
            weeks[week_key] = {"monday": monday, "dates": []}
        weeks[week_key]["dates"].append(dt)

    # UIで表示
    changes = {}
    for week_key in sorted(weeks.keys()):
        week_info = weeks[week_key]
        monday = week_info["monday"]
        dates = week_info["dates"]
        dates_str = ", ".join(d.strftime("%m/%d(%a)") for d in dates)
        week_label = f"{monday.strftime('%Y-%m-%d')}週 ({dates_str})"

        # 週の状態判定: 全日付がactiveか
        date_strs = [d.isoformat() for d in dates]
        current_active = all(existing_map.get(ds, 1) for ds in date_strs)

        is_on = st.checkbox(
            week_label,
            value=current_active,
            key=f"wk_week_{section}_{week_key[0]}_{week_key[1]}",
        )

        for ds in date_strs:
            if is_on != bool(existing_map.get(ds, 1)):
                changes[ds] = is_on

    if st.button("対象日を保存", type="primary", key=f"save_target_dates_{section}"):
        if changes:
            # 全日付リストを set_target_dates で一括設定
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
            # 初回: 既存データがない場合もセット
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
            sc1, sc2 = st.columns(2)
            with sc1:
                start_time = st.text_input("開始時間", placeholder="09:00")
            with sc2:
                end_time = st.text_input("終了時間", placeholder="12:00")
            req_count = st.number_input("必要人数", min_value=1, max_value=10, value=1)
            if st.form_submit_button("追加", use_container_width=True):
                if not slot_name.strip():
                    st.error("スロット名を入力してください")
                elif not start_time.strip() or not end_time.strip():
                    st.error("開始・終了時間を入力してください")
                else:
                    add_weekday_slot(section, slot_name.strip(), day_of_week,
                                     start_time.strip(), end_time.strip(), req_count)
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
                    e_start = st.text_input("開始時間", value=s["start_time"], key=f"se_start_{s['id']}")
                    e_end = st.text_input("終了時間", value=s["end_time"], key=f"se_end_{s['id']}")
                    e_req = st.number_input("必要人数", min_value=1, max_value=10,
                                            value=s["required_count"], key=f"se_req_{s['id']}")
                    fc1, fc2 = st.columns(2)
                    with fc1:
                        if st.form_submit_button("保存"):
                            update_weekday_slot(s["id"], slot_name=e_name.strip(),
                                                start_time=e_start.strip(), end_time=e_end.strip(),
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


def _render_schedule(section: str, cfg: dict, assigned_doctor_ids: list, days_of_week: list):
    """スケジュール作成タブ"""
    st.subheader("スケジュール作成")

    # 対象月選択
    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(4)]
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

    # 自動生成ボタン
    if st.button("自動生成", type="primary", key=f"auto_gen_{section}"):
        result = solve_weekday_schedule(target_dates, active_slots, assigned_doctors, prefs)
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
                current_assigned = existing_map.get(ds, {}).get(slot["id"], [])
                for k in range(slot["required_count"]):
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
                assigned = []
                for k in range(slot["required_count"]):
                    val = st.session_state.get(f"sched_{section}_{ds}_{slot['id']}_{k}", 0)
                    if val and val != 0:
                        assigned.append(val)
                assignments[ds][slot["id"]] = assigned

        batch_save_weekday_assignments(target_month, section, assignments)
        st.success("スケジュールを保存しました")
        st.rerun()
