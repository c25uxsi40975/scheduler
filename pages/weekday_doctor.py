"""
医員の平日セクションビュー
希望入力・スケジュール確認・シフト交換
"""
from datetime import date
from dateutil.relativedelta import relativedelta
import requests
import streamlit as st
import streamlit.components.v1 as components

from audit import log_event
from database import (
    get_doctors,
    get_weekday_config_by_section,
    get_active_target_dates,
    get_weekday_preference, get_weekday_preferences, upsert_weekday_preference,
    get_weekday_schedule,
    get_weekday_slots,
    get_weekday_open_section, get_weekday_deadline,
    get_weekday_readjust_dates,
    get_weekday_confirmed_months,
    get_weekday_schedule_view_mode,
    execute_swap, get_swap_history,
    get_specimen_assignee,
)
from components.display_utils import build_display_name_map
from components.schedule_image import generate_weekday_schedule_image
from components.schedule_viewer import _VIEWER_SCRIPT
from components.weekday_calendar import render_weekday_calendar

DAY_NAMES = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}


def render(doctor: dict, section: str):
    """医員の平日外勤セクション画面"""
    cfg = get_weekday_config_by_section(section)
    if not cfg:
        st.error("セクション情報が見つかりません")
        return

    clinic_name = cfg["clinic_name"]

    tab1, tab2, tab3 = st.tabs(["スケジュール確認", "希望入力", "シフト交換"])

    with tab1:
        _render_schedule_view(doctor, section, cfg)
    with tab2:
        _render_preference_input(doctor, section, cfg)
    with tab3:
        _render_shift_swap(doctor, section, cfg)


def _render_preference_input(doctor: dict, section: str, cfg: dict):
    """希望入力タブ"""
    is_open = get_weekday_open_section(section)
    deadline = get_weekday_deadline(section)

    if not is_open:
        st.info("希望入力は現在公開されていません。")
        return

    if deadline:
        try:
            dl_date = date.fromisoformat(deadline)
            if date.today() > dl_date:
                st.warning(f"入力期限（{deadline}）を過ぎています。")
                return
            st.caption(f"入力期限: {deadline}")
        except ValueError:
            pass

    active_dates = get_active_target_dates(section)
    if not active_dates:
        st.info("対象日が設定されていません。")
        return

    # 再調整対象日が設定されている場合はその日のみ表示
    readjust_dates = get_weekday_readjust_dates(section)
    if readjust_dates:
        readjust_set = set(readjust_dates)
        active_dates = [ds for ds in active_dates if ds in readjust_set]
        if not active_dates:
            st.info("現在入力対象の日付がありません。")
            return
        st.info(f"スケジュール再調整に伴い、以下の **{len(active_dates)}日** について希望を入力してください。")
    else:
        # 確定済み月の日付を除外（再調整モードでは管理者が明示的に開いた日付なのでスキップ）
        confirmed_months = set(get_weekday_confirmed_months(section))
        if confirmed_months:
            active_dates = [ds for ds in active_dates if ds[:7] not in confirmed_months]
            if not active_dates:
                st.info("すべての対象月のスケジュールが確定済みのため、希望入力はできません。")
                return

    pref = get_weekday_preference(doctor["id"], section)
    existing_ng = set(pref.get("ng_dates", []) if pref else [])
    existing_avoid = set(pref.get("avoid_dates", []) if pref else [])
    existing_free = pref.get("free_text", "") if pref else ""

    SCHEDULE_STATUS = ["○", "△", "×"]

    with st.form(f"weekday_pref_{section}_{doctor['id']}"):
        st.write("各日の希望を入力してください（○=可能　△=できれば避けたい　×=NG）")
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
                    key=f"wkpref_{section}_{doctor['id']}_{ds}",
                )

        free_text = st.text_area(
            "備考",
            value=existing_free,
            placeholder="例: 第3週は学会のため不可",
            key=f"wkpref_free_{section}_{doctor['id']}",
        )

        if st.form_submit_button("希望を保存", type="primary"):
            new_ng = []
            new_avoid = []
            for ds in active_dates:
                val = st.session_state.get(f"wkpref_{section}_{doctor['id']}_{ds}", "○")
                if val == "×":
                    new_ng.append(ds)
                elif val == "△":
                    new_avoid.append(ds)

            # 再調整モードの場合、対象外の既存希望を維持
            if readjust_dates:
                active_set = set(active_dates)
                for ds in existing_ng:
                    if ds not in active_set and ds not in new_ng:
                        new_ng.append(ds)
                for ds in existing_avoid:
                    if ds not in active_set and ds not in new_avoid:
                        new_avoid.append(ds)

            upsert_weekday_preference(
                doctor["id"], section,
                ng_dates=new_ng,
                avoid_dates=new_avoid,
                free_text=free_text,
            )

            # メール通知
            gas_url = st.secrets.get("gas_webapp_url", "")
            if gas_url:
                # 希望入力確認メール
                date_summary = ""
                if new_ng:
                    date_summary += f"NG日: {', '.join(new_ng)}\n"
                if new_avoid:
                    date_summary += f"避けたい日: {', '.join(new_avoid)}\n"
                if not date_summary:
                    date_summary = "すべて○"
                try:
                    requests.post(gas_url, json={
                        "action": "weekday_preference_confirmed",
                        "section": section,
                        "clinic_name": cfg["clinic_name"],
                        "doctor_name": doctor["name"],
                        "doctor_email": doctor.get("email", ""),
                        "date_summary": date_summary,
                        "free_text": free_text or "",
                    }, timeout=10)
                except requests.RequestException:
                    pass

                # 全員入力完了チェック
                try:
                    assigned_ids = cfg.get("assigned_doctors", [])
                    all_prefs = get_weekday_preferences(section)
                    submitted_ids = {p["doctor_id"] for p in all_prefs}
                    if assigned_ids and all(did in submitted_ids for did in assigned_ids):
                        requests.post(gas_url, json={
                            "action": "weekday_all_preferences_complete",
                            "section": section,
                            "clinic_name": cfg["clinic_name"],
                            "doctor_count": len(assigned_ids),
                        }, timeout=10)
                except (requests.RequestException, Exception):
                    pass

            st.success("希望を保存しました")
            st.rerun()


def _render_schedule_view(doctor: dict, section: str, cfg: dict):
    """スケジュール確認タブ"""
    # 表示モード切替: 月ごと / すべて表示
    display_mode = st.radio(
        "表示範囲",
        ["月ごと", "すべて表示"],
        horizontal=True,
        key=f"wkdoc_display_mode_{section}",
    )

    if display_mode == "すべて表示":
        _render_schedule_all(doctor, section, cfg)
    else:
        _render_schedule_monthly(doctor, section, cfg)


def _render_schedule_monthly(doctor: dict, section: str, cfg: dict):
    """月ごとのスケジュール表示"""
    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(-1, 14)]
    view_month = st.selectbox("月を選択", months, index=1, key=f"wkdoc_view_month_{section}")

    _render_month_schedule(doctor, section, cfg, view_month)


def _render_schedule_all(doctor: dict, section: str, cfg: dict):
    """確定済み全月のスケジュールをカレンダー形式で一覧表示"""
    confirmed_months = sorted(get_weekday_confirmed_months(section))
    if not confirmed_months:
        st.info("確定済みのスケジュールはありません。")
        return

    from scheduling_utils import is_nenmatsu_nenshi

    # 全月の自分の割り当て数を集計
    all_my_count = 0
    all_doctors = get_doctors()
    slots = get_weekday_slots(section)

    for ym in confirmed_months:
        schedule = get_weekday_schedule(ym, section)
        if not schedule:
            continue
        schedule = [r for r in schedule
                    if not is_nenmatsu_nenshi(date.fromisoformat(r["date"]))]
        if not schedule:
            continue

        my_assignments = [r for r in schedule if r["doctor_id"] == doctor["id"]]
        all_my_count += len(my_assignments)

        month_label = ym.replace("-", "年") + "月"
        st.subheader(month_label)

        if my_assignments:
            st.write(f"**あなたの割り当て: {len(my_assignments)}回**")

        render_weekday_calendar(schedule, slots, ym, all_doctors,
                                highlight_doctor_id=doctor["id"])
        st.markdown("---")

    st.caption(f"確定済み全期間の合計割り当て: {all_my_count}回")


def _render_month_schedule(doctor: dict, section: str, cfg: dict, view_month: str):
    """指定月のスケジュールを表示（共通処理）"""
    schedule = get_weekday_schedule(view_month, section)
    if not schedule:
        st.info("この月のスケジュールはまだありません。")
        return

    from scheduling_utils import is_nenmatsu_nenshi
    schedule = [r for r in schedule
                if not is_nenmatsu_nenshi(date.fromisoformat(r["date"]))]
    if not schedule:
        st.info("この月のスケジュールはまだありません。")
        return

    slots = get_weekday_slots(section)

    # 自分の割り当てをハイライト
    my_assignments = [r for r in schedule if r["doctor_id"] == doctor["id"]]
    if my_assignments:
        st.write(f"**あなたの割り当て: {len(my_assignments)}回**")
        specimen_alerts = []
        for r in sorted(my_assignments, key=lambda x: x["date"]):
            try:
                dt = date.fromisoformat(r["date"])
                date_label = dt.strftime("%m/%d(%a)")
            except ValueError:
                date_label = r["date"]
            specimen_mark = ""
            if cfg.get("specimen_enabled"):
                spec = get_specimen_assignee(section, r["date"], schedule)
                if spec and spec["doctor_id"] == doctor["id"]:
                    if spec["conflict"]:
                        other_names = [d["doctor_name"] for d in spec["conflict_doctors"]
                                       if d["doctor_id"] != doctor["id"]]
                        specimen_mark = f" 🧪同意書・検体確認（{', '.join(other_names)}先生と要相談）"
                        specimen_alerts.append((date_label, spec))
                    else:
                        specimen_mark = " 🧪同意書・検体確認"
                        specimen_alerts.append((date_label, spec))
            st.write(f"　{date_label}　{r['slot_name']}{specimen_mark}")
        if specimen_alerts:
            for date_label, spec in specimen_alerts:
                if spec["conflict"]:
                    other_names = [d["doctor_name"] for d in spec["conflict_doctors"]
                                   if d["doctor_id"] != doctor["id"]]
                    st.warning(f"🧪 {date_label} 同意書・検体確認（同学年のため{', '.join(other_names)}先生と要相談）")
                else:
                    st.info(f"🧪 {date_label} 同意書・検体確認担当日です")
    else:
        st.write("この月の割り当てはありません")

    # 表示モード判定
    view_mode = get_weekday_schedule_view_mode(section)

    st.markdown("---")
    st.subheader("全体スケジュール")

    if view_mode == "calendar":
        # カレンダー表示（全メンバー表示）
        all_doctors = get_doctors()
        render_weekday_calendar(schedule, slots, view_month, all_doctors,
                                highlight_doctor_id=doctor["id"])
    else:
        # テーブル表示（現行：画像）
        img_data = generate_weekday_schedule_image(
            schedule, slots, view_month,
            highlight_doctor_id=doctor["id"],
        )
        if img_data:
            st.image(img_data, use_container_width=True)
            components.html(_VIEWER_SCRIPT, height=0)


def _render_shift_swap(doctor: dict, section: str, cfg: dict):
    """シフト交換タブ — 任意の2医員のシフトを交換可能"""
    st.write("メンバー同士のシフトを交換できます")

    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(14)]
    swap_month = st.selectbox("月を選択", months, key=f"wkdoc_swap_month_{section}")

    schedule = get_weekday_schedule(swap_month, section)
    if not schedule:
        st.info("この月のスケジュールがありません。")
        return

    if len(schedule) < 2:
        st.info("交換可能なシフトがありません。")
        return

    # NG/△の事前計算
    prefs = get_weekday_preferences(section)
    ng_set = set()
    avoid_set = set()
    for pref in prefs:
        did = pref.get("doctor_id")
        for ds in (pref.get("ng_dates") or []):
            ng_set.add((did, ds))
        for ds in (pref.get("avoid_dates") or []):
            avoid_set.add((did, ds))

    def _label(r):
        try:
            dt = date.fromisoformat(r["date"])
            base = f"{dt.strftime('%m/%d(%a)')} {r['slot_name']} - {r['doctor_name']}"
        except ValueError:
            base = f"{r['date']} {r['slot_name']} - {r['doctor_name']}"
        did, ds = r["doctor_id"], r["date"]
        if (did, ds) in ng_set:
            return f"⛔ {base}【NG】"
        if (did, ds) in avoid_set:
            return f"⚠ {base}【△】"
        return base

    # Step 1: 交換元のシフトを選択（全メンバー対象）
    selected_a = st.selectbox(
        "交換元のシフト",
        schedule,
        format_func=_label,
        key=f"swap_a_{section}",
    )

    # Step 2: 交換先（交換元と異なる医員のみ）
    if selected_a:
        candidates = [r for r in schedule if r["doctor_id"] != selected_a["doctor_id"]]
        if not candidates:
            st.info("交換先の候補がありません。")
            return

        selected_b = st.selectbox(
            "交換先のシフト",
            candidates,
            format_func=_label,
            key=f"swap_b_{section}",
        )
    else:
        return

    if selected_a and selected_b:
        st.markdown("---")
        st.write("**交換内容の確認**")
        st.write(f"操作者: {doctor['name']}")
        # 交換後の相手先日付でNG/△チェック
        a_did, b_did = selected_a["doctor_id"], selected_b["doctor_id"]
        a_to_date, b_to_date = selected_b["date"], selected_a["date"]
        swap_warnings = []
        if (a_did, a_to_date) in ng_set:
            swap_warnings.append(f"⛔ {selected_a['doctor_name']} は {a_to_date} がNG日です")
        elif (a_did, a_to_date) in avoid_set:
            swap_warnings.append(f"⚠ {selected_a['doctor_name']} は {a_to_date} が△（できれば避けたい）日です")
        if (b_did, b_to_date) in ng_set:
            swap_warnings.append(f"⛔ {selected_b['doctor_name']} は {b_to_date} がNG日です")
        elif (b_did, b_to_date) in avoid_set:
            swap_warnings.append(f"⚠ {selected_b['doctor_name']} は {b_to_date} が△（できれば避けたい）日です")

        st.write(f"{selected_a['doctor_name']}: {_label(selected_a)} → {_label(selected_b)}")
        st.write(f"{selected_b['doctor_name']}: {_label(selected_b)} → {_label(selected_a)}")

        if swap_warnings:
            for w in swap_warnings:
                st.warning(w)

        if st.button("交換を実行", type="primary", key=f"do_swap_{section}"):
            execute_swap(
                swap_month, section,
                requester_id=selected_a["doctor_id"],
                original_date=selected_a["date"],
                original_slot_id=selected_a["slot_id"],
                target_id=selected_b["doctor_id"],
                target_date=selected_b["date"],
                target_slot_id=selected_b["slot_id"],
                actor_id=doctor["id"],
            )

            # 監査ログ
            log_event(
                "shift_swap",
                actor=doctor["name"],
                detail=(
                    f"{cfg['clinic_name']} {swap_month}: "
                    f"{selected_a['doctor_name']}({selected_a['date']}) ↔ "
                    f"{selected_b['doctor_name']}({selected_b['date']})"
                ),
            )

            # 通知（GAS webhook）
            gas_url = st.secrets.get("gas_webapp_url", "")
            if gas_url:
                try:
                    requests.post(gas_url, json={
                        "action": "shift_swap_executed",
                        "section": section,
                        "clinic_name": cfg["clinic_name"],
                        "actor_name": doctor["name"],
                        "actor_id": doctor["id"],
                        "requester_name": selected_a["doctor_name"],
                        "requester_shift": _label(selected_a),
                        "target_name": selected_b["doctor_name"],
                        "target_shift": _label(selected_b),
                        "subadmin_doctors": cfg.get("subadmin_doctors", []),
                    }, timeout=10)
                except requests.RequestException:
                    pass

            st.success("シフト交換が完了しました")
            st.rerun()

    # 交換履歴
    with st.expander("交換履歴"):
        history = get_swap_history(swap_month, section)
        if history:
            for h in history:
                actor = h.get("actor_name", "")
                actor_info = f"[{actor}] " if actor else ""
                st.write(
                    f"{h.get('executed_at', '')}　{actor_info}"
                    f"{h.get('requester_name', '')}({h.get('original_date', '')}) ↔ "
                    f"{h.get('target_name', '')}({h.get('target_date', '')})"
                )
        else:
            st.info("交換履歴はありません")
