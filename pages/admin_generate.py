"""管理者: スケジュール生成タブ"""
import streamlit as st
import pandas as pd
import requests
from datetime import date
from database import (
    get_doctors, get_clinics, get_all_preferences,
    get_affinities, get_schedules, save_schedule, confirm_schedule,
    delete_schedule, update_schedule_assignments,
    get_clinic_date_overrides, get_all_confirmed_schedules,
    delete_old_schedules,
)
from optimizer import get_target_saturdays, generate_multiple_plans
from components.schedule_table import render_schedule_table


def _send_confirmation_notification(target_month, sched):
    """GAS Web App経由で確定通知メールを送信"""
    gas_url = st.secrets.get("gas_webapp_url", "")
    if not gas_url:
        return
    try:
        requests.post(gas_url, json={
            "action": "schedule_confirmed",
            "year_month": target_month,
            "plan_name": sched["plan_name"],
        }, timeout=10)
    except requests.RequestException:
        st.warning("メール通知の送信に失敗しました。スケジュールは確定済みです。")


def _calc_previous_earnings(clinics, target_year, target_month):
    """過去の全確定スケジュールから累計報酬を算出（対象月より前の月のみ）"""
    target_ym = f"{target_year:04d}-{target_month:02d}"
    fee_map = {c["id"]: c["fee"] for c in clinics}
    earnings = {}
    confirmed = get_all_confirmed_schedules()
    months_used = set()
    for sched in confirmed:
        if sched["year_month"] < target_ym:
            months_used.add(sched["year_month"])
            for a in sched["assignments"]:
                did = a["doctor_id"]
                earnings[did] = earnings.get(did, 0) + fee_map.get(a["clinic_id"], 0)
    return earnings, sorted(months_used)


def render(target_month, year, month):
    st.header(f"スケジュール生成 ({target_month})")

    doctors = get_doctors()
    clinics = get_clinics()
    saturdays = get_target_saturdays(year, month)
    prefs = get_all_preferences(target_month)
    affinities = get_affinities()

    if not doctors:
        st.warning("医員が登録されていません")
    elif not clinics:
        st.warning("外勤先が登録されていません")
    elif not saturdays:
        st.warning("対象月に土曜日（祝日除く）がありません")
    else:
        st.write(f"医員: {len(doctors)}人 | 外勤先: {len(clinics)}ヶ所 | 対象土曜: {len(saturdays)}日")

        if not prefs:
            st.warning("希望入力がまだありません。入力なしで生成しますか？")

        # 過去の全確定スケジュールから累計報酬を算出
        previous_earnings, months_used = _calc_previous_earnings(clinics, year, month)

        if previous_earnings:
            st.info(f"過去の確定スケジュール({len(months_used)}ヶ月分: {', '.join(months_used)})の累計報酬を考慮します")

        if st.button("スケジュール案を生成", type="primary", use_container_width=True):
            with st.spinner("最適化計算中..."):
                overrides = get_clinic_date_overrides(target_month)
                plans = generate_multiple_plans(
                    doctors, clinics, saturdays, prefs, affinities,
                    previous_earnings=previous_earnings,
                    date_overrides=overrides,
                )

            if not plans:
                st.error("制約を満たすスケジュールが見つかりません。制約条件を見直してください。")
            else:
                st.success(f"{len(plans)}件の案を生成しました")

                for plan in plans:
                    save_schedule(
                        target_month,
                        plan["plan_name"],
                        plan["assignments"],
                        plan["total_variance"],
                        plan["satisfaction_score"]
                    )

                st.rerun()

    # 生成済みスケジュール表示
    schedules = get_schedules(target_month)
    if schedules:
        st.markdown("---")
        st.subheader("生成済みスケジュール案")

        # データを一度だけ取得してローカル変数に保持（冗長なAPI呼出を排除）
        _clinics = get_clinics()
        _doctors = get_doctors()
        fee_map = {c["id"]: c["fee"] for c in _clinics}
        clinic_map = {c["id"]: c for c in _clinics}

        for sched in schedules:
            confirmed = "[確定]" if sched["is_confirmed"] else ""
            with st.expander(
                f"{sched['plan_name']} {confirmed} "
                f"(分散: {sched['total_variance']:.0f}, "
                f"満足度: {sched['satisfaction_score']:.1f})",
                expanded=sched["is_confirmed"]
            ):
                # 手動調整モード
                editing_key = f"editing_sched_{sched['id']}"
                is_editing = st.session_state.get(editing_key, False)

                if is_editing:
                    _render_edit_mode(sched, _doctors, clinic_map, editing_key)
                else:
                    render_schedule_table(sched, _doctors, _clinics)

                    # 医員別統計
                    st.write("**医員別統計:**")
                    doc_stats = {}
                    for a in sched["assignments"]:
                        did = a["doctor_id"]
                        if did not in doc_stats:
                            doc_stats[did] = {"回数": 0, "報酬合計": 0}
                        doc_stats[did]["回数"] += 1
                        doc_stats[did]["報酬合計"] += fee_map.get(a["clinic_id"], 0)

                    stat_rows = []
                    for d in _doctors:
                        s = doc_stats.get(d["id"], {"回数": 0, "報酬合計": 0})
                        stat_rows.append({
                            "医員": d["name"],
                            "外勤回数": s["回数"],
                            "報酬合計": f"¥{s['報酬合計']:,}",
                        })

                    df_stat = pd.DataFrame(stat_rows)
                    st.dataframe(df_stat, use_container_width=True, hide_index=True)

                    # アクションボタン
                    btn_cols = st.columns(3)
                    with btn_cols[0]:
                        if not sched["is_confirmed"]:
                            if st.button("確定する", key=f"confirm_{sched['id']}",
                                         type="primary"):
                                confirm_schedule(sched["id"])
                                delete_old_schedules(months_to_keep=4)
                                _send_confirmation_notification(target_month, sched)
                                st.success("確定しました！")
                                st.rerun()
                        else:
                            st.success("確定済み")
                    with btn_cols[1]:
                        if st.button("手動調整", key=f"edit_{sched['id']}"):
                            st.session_state[editing_key] = True
                            st.rerun()
                    with btn_cols[2]:
                        if not sched["is_confirmed"]:
                            if st.button("削除", key=f"del_{sched['id']}", type="secondary"):
                                st.session_state[f"confirm_del_sched_{sched['id']}"] = True

                    # 削除確認
                    if st.session_state.get(f"confirm_del_sched_{sched['id']}"):
                        st.warning(f"「{sched['plan_name']}」を削除しますか？")
                        dc1, dc2 = st.columns(2)
                        with dc1:
                            if st.button("削除する", key=f"do_del_{sched['id']}", type="primary"):
                                delete_schedule(sched["id"])
                                st.session_state.pop(f"confirm_del_sched_{sched['id']}", None)
                                st.rerun()
                        with dc2:
                            if st.button("キャンセル", key=f"cancel_del_{sched['id']}"):
                                st.session_state.pop(f"confirm_del_sched_{sched['id']}", None)
                                st.rerun()


def _render_edit_mode(sched, doctors, clinic_map, editing_key):
    """スケジュールの手動調整UI"""
    st.info("手動調整モード: 各スロットの担当医員を変更できます")

    assignments = sched["assignments"]

    # assignments を (date, clinic_id) → doctor_id のマップに変換
    slot_map = {}
    for a in assignments:
        slot_map[(a["date"], a["clinic_id"])] = a["doctor_id"]

    # スケジュールに含まれる日付と外勤先を抽出
    dates = sorted(set(a["date"] for a in assignments))
    clinics_in_sched = sorted(
        set(a["clinic_id"] for a in assignments),
        key=lambda cid: clinic_map.get(cid, {}).get("name", "")
    )

    doctor_options = [("", "（割り当てなし）")] + [(d["id"], d["name"]) for d in doctors]

    new_assignments = []
    for ds in dates:
        d_obj = date.fromisoformat(ds)
        st.write(f"**{d_obj.strftime('%m/%d(%a)')}**")
        cols = st.columns(min(len(clinics_in_sched), 4))
        for i, cid in enumerate(clinics_in_sched):
            if (ds, cid) not in slot_map:
                continue
            cname = clinic_map.get(cid, {}).get("name", f"外勤先{cid}")
            current_did = slot_map.get((ds, cid))
            with cols[i % len(cols)]:
                # 現在の担当医員のインデックスを取得
                current_idx = 0
                for j, (did, _) in enumerate(doctor_options):
                    if did == current_did:
                        current_idx = j
                        break

                selected = st.selectbox(
                    cname,
                    doctor_options,
                    index=current_idx,
                    format_func=lambda x: x[1],
                    key=f"slot_{sched['id']}_{ds}_{cid}",
                )
                if selected[0]:  # 割り当てありの場合
                    new_assignments.append({
                        "date": ds,
                        "clinic_id": cid,
                        "doctor_id": selected[0],
                    })

    btn_cols = st.columns(2)
    with btn_cols[0]:
        if st.button("変更を保存", key=f"save_edit_{sched['id']}", type="primary"):
            update_schedule_assignments(sched["id"], new_assignments)
            st.session_state.pop(editing_key, None)
            st.success("保存しました")
            st.rerun()
    with btn_cols[1]:
        if st.button("キャンセル", key=f"cancel_edit_{sched['id']}"):
            st.session_state.pop(editing_key, None)
            st.rerun()
