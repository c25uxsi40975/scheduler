"""
医員の平日セクションビュー
希望入力・スケジュール確認・シフト変更
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
    execute_shift_change, get_shift_change_history, resync_weekday_calendar,
    get_specimen_assignee,
)
from database.weekday import DOW_LABELS_JA
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

    tab1, tab2, tab3 = st.tabs(
        ["スケジュール確認", "希望入力", "シフト変更"]
    )

    with tab1:
        _render_schedule_view(doctor, section, cfg)
    with tab2:
        _render_preference_input(doctor, section, cfg)
    with tab3:
        _render_shift_change(doctor, section, cfg)


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
    all_doctors = get_doctors(active_only=False)  # 平日は土曜の無効化(is_active)に波及させない
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
                        others = [
                            f"{DOW_LABELS_JA.get(wd, '?')}曜の{d['doctor_name']}先生"
                            for d in spec["conflict_doctors"]
                            if d["doctor_id"] != doctor["id"]
                            for wd in d.get("weekdays", [])
                        ]
                        specimen_mark = f" 🧪同意書・検体確認（{'・'.join(others)}と相談してください）"
                        specimen_alerts.append((date_label, spec))
                    else:
                        specimen_mark = " 🧪同意書・検体確認"
                        specimen_alerts.append((date_label, spec))
            st.write(f"　{date_label}　{r['slot_name']}{specimen_mark}")
        if specimen_alerts:
            for date_label, spec in specimen_alerts:
                if spec["conflict"]:
                    others = [
                        f"{DOW_LABELS_JA.get(wd, '?')}曜の{d['doctor_name']}先生"
                        for d in spec["conflict_doctors"]
                        if d["doctor_id"] != doctor["id"]
                        for wd in d.get("weekdays", [])
                    ]
                    st.warning(f"🧪 {date_label} 同意書・検体確認（{'・'.join(others)}と相談してください）")
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
        all_doctors = get_doctors(active_only=False)  # 平日は土曜の無効化(is_active)に波及させない
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


def _render_shift_change(doctor: dict, section: str, cfg: dict):
    """シフト変更タブ — 指定日の医員を別の医員に差し替える（一方向）"""
    st.write("指定した日のシフトを別の医員に差し替えできます")
    st.caption("交換（双方向）と異なり、1人を別の医員に置き換える一方向の更新です。")

    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(14)]
    change_month = st.selectbox("月を選択", months, key=f"wkdoc_change_month_{section}")

    schedule = get_weekday_schedule(change_month, section)
    if not schedule:
        st.info("この月のスケジュールがありません。")
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

    # Step 1: 変更対象のシフトを選択
    selected_src = st.selectbox(
        "変更対象のシフト（変更元）",
        schedule,
        format_func=_label,
        key=f"change_src_{section}",
    )
    if not selected_src:
        return

    # Step 2: 変更後の医員を選択（割り当て対象医員から、変更元と同日同スロットの既存割当者を除外）
    assigned_ids = cfg.get("assigned_doctors", []) or []
    all_doctors = get_doctors(active_only=False)
    name_map = {d["id"]: d["name"] for d in all_doctors}

    occupied_ids = {
        r["doctor_id"] for r in schedule
        if r["date"] == selected_src["date"]
        and r["slot_id"] == selected_src["slot_id"]
    }

    candidate_ids = [
        did for did in assigned_ids
        if did != selected_src["doctor_id"] and did not in occupied_ids
    ]
    if not candidate_ids:
        st.info("差し替え可能な医員がいません（同日同スロットの既存割当者は除外されます）。")
        return

    def _doc_label(did):
        ds = selected_src["date"]
        name = name_map.get(did, f"ID:{did}")
        if (did, ds) in ng_set:
            return f"⛔ {name}【NG】"
        if (did, ds) in avoid_set:
            return f"⚠ {name}【△】"
        return name

    new_doctor_id = st.selectbox(
        "変更後の医員（変更先）",
        candidate_ids,
        format_func=_doc_label,
        key=f"change_dst_{section}",
    )

    st.markdown("---")
    st.write("**変更内容の確認**")
    st.write(f"操作者: {doctor['name']}")
    try:
        dt = date.fromisoformat(selected_src["date"])
        date_disp = dt.strftime("%m/%d(%a)")
    except ValueError:
        date_disp = selected_src["date"]
    new_name = name_map.get(new_doctor_id, "")
    st.write(
        f"{date_disp} {selected_src['slot_name']}: "
        f"{selected_src['doctor_name']} → {new_name}"
    )

    # NG警告
    if (new_doctor_id, selected_src["date"]) in ng_set:
        st.warning(f"⛔ {new_name}先生はこの日をNGに設定しています。")
    elif (new_doctor_id, selected_src["date"]) in avoid_set:
        st.info(f"⚠ {new_name}先生はこの日を△（避けたい）に設定しています。")

    if st.button("変更を実行", type="primary", key=f"do_change_{section}"):
        try:
            execute_shift_change(
                change_month, section,
                date=selected_src["date"],
                slot_id=selected_src["slot_id"],
                original_doctor_id=selected_src["doctor_id"],
                new_doctor_id=new_doctor_id,
                actor_id=doctor["id"],
            )
        except ValueError as e:
            st.error(str(e))
            return

        # カレンダー再同期（確定済みならシフト＋検体確認イベントを更新。メール通知なし）
        resync_weekday_calendar(section, cfg.get("clinic_name", ""), year_months=[change_month])

        # 監査ログ
        log_event(
            "shift_change",
            actor=doctor["name"],
            detail=(
                f"{cfg['clinic_name']} {change_month} "
                f"{selected_src['date']} {selected_src['slot_name']}: "
                f"{selected_src['doctor_name']} → {new_name}"
            ),
        )

        # 通知（GAS webhook）— 変更元・変更先・管理者(副管理者)へ
        gas_url = st.secrets.get("gas_webapp_url", "")
        if gas_url:
            original_doctor = next(
                (d for d in all_doctors if d["id"] == selected_src["doctor_id"]),
                {},
            )
            new_doctor = next(
                (d for d in all_doctors if d["id"] == new_doctor_id),
                {},
            )
            try:
                requests.post(gas_url, json={
                    "action": "shift_change_executed",
                    "section": section,
                    "clinic_name": cfg["clinic_name"],
                    "year_month": change_month,
                    "date": selected_src["date"],
                    "slot_name": selected_src["slot_name"],
                    "actor_id": doctor["id"],
                    "actor_name": doctor["name"],
                    "original_doctor_id": selected_src["doctor_id"],
                    "original_doctor_name": selected_src["doctor_name"],
                    "original_doctor_email": original_doctor.get("email", ""),
                    "new_doctor_id": new_doctor_id,
                    "new_doctor_name": new_name,
                    "new_doctor_email": new_doctor.get("email", ""),
                    "subadmin_doctors": cfg.get("subadmin_doctors", []),
                }, timeout=10)
            except requests.RequestException:
                pass

        st.success("シフト変更が完了しました")
        st.rerun()

    # 変更履歴
    with st.expander("変更履歴"):
        history = get_shift_change_history(change_month, section)
        if history:
            for h in sorted(history, key=lambda x: x.get("executed_at", ""), reverse=True):
                actor = h.get("actor_name", "")
                actor_info = f"[{actor}] " if actor else ""
                st.write(
                    f"{h.get('executed_at', '')}　{actor_info}"
                    f"{h.get('date', '')} {h.get('slot_name', '')}: "
                    f"{h.get('original_doctor_name', '')} → "
                    f"{h.get('new_doctor_name', '')}"
                )
        else:
            st.info("変更履歴はありません")
