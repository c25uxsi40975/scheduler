"""管理者: スケジュール生成タブ"""
import streamlit as st
import requests
from datetime import date
from database import (
    get_doctors, get_clinics, get_all_preferences,
    get_affinities, get_schedules, save_schedule,
    unconfirm_schedule, delete_schedule,
    get_clinic_date_overrides, get_all_confirmed_schedules, get_double_shift_pairs,
)
from optimizer import get_target_saturdays, diagnose_infeasibility
from pipeline import run_integrated_pipeline
from components.schedule_table import render_schedule_table, render_doctor_view_table, render_doctor_stats_table
from components.display_utils import build_display_name_map


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
    if not st.session_state.get("admin_authenticated"):
        st.stop()
    st.header(f"スケジュール生成 ({target_month})")

    if st.session_state.get("_unfilled_warn"):
        warns = st.session_state.pop("_unfilled_warn")
        st.warning(
            "人手不足のため一部スロットを未割当のまま生成しました。"
            "下書き編集タブで手動割当してください:\n\n"
            + "\n".join(f"- {w}" for w in warns)
        )

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
            with st.spinner("ML適合性スコア計算 + 最適化中..."):
                overrides = get_clinic_date_overrides(target_month)
                confirmed = get_all_confirmed_schedules()
                ds_pairs = get_double_shift_pairs(active_only=True)
                result = run_integrated_pipeline(
                    target_month, year, month,
                    doctors, clinics, confirmed, prefs, affinities,
                    overrides, previous_earnings=previous_earnings,
                    double_shift_pairs=ds_pairs,
                )
                plans = result["plans"]

            if not plans:
                st.error("制約を満たすスケジュールが見つかりません。制約条件を見直してください。")
                diag = diagnose_infeasibility(
                    doctors, clinics, saturdays, prefs, affinities,
                    date_overrides=overrides, ds_pairs=ds_pairs,
                )
                with st.expander("診断情報", expanded=True):
                    for line in diag:
                        st.write(f"- {line}")
            else:
                clinic_name_map = {c["id"]: c["name"] for c in clinics}
                unfilled_warnings = []
                for plan in plans:
                    unfilled = plan.get("unfilled_slots") or []
                    plan_name = plan["plan_name"]
                    if unfilled:
                        n = sum(s["shortage"] for s in unfilled)
                        plan_name = f"{plan_name} ({n}枠未割当)"
                        slot_descs = "、".join(
                            f"{s['date']} {clinic_name_map.get(s['clinic_id'], '?')}"
                            for s in unfilled
                        )
                        unfilled_warnings.append(f"{plan['plan_name']}: {slot_descs}")
                    save_schedule(
                        target_month,
                        plan_name,
                        plan["assignments"],
                        plan["total_variance"],
                        plan["satisfaction_score"]
                    )

                if unfilled_warnings:
                    st.session_state["_unfilled_warn"] = unfilled_warnings
                st.session_state["_toast_msg"] = f"{len(plans)}件の案を生成しました"
                st.rerun()

    # 生成済みスケジュール表示
    schedules = get_schedules(target_month)
    if schedules:
        st.markdown("---")
        st.subheader("生成済みスケジュール案")

        # データを一度だけ取得してローカル変数に保持（冗長なAPI呼出を排除）
        _clinics = get_clinics()
        _doctors = get_doctors()
        clinic_map = {c["id"]: c for c in _clinics}
        doc_name_map = build_display_name_map(_doctors)
        clinic_name_map = {c["id"]: c["name"] for c in _clinics}

        # △日マップ（避けたい日）
        avoid_map = {}
        for p in prefs:
            avoid = p.get("avoid_dates") or []
            if avoid:
                avoid_map[p["doctor_id"]] = set(avoid)

        # 下書き（未確定）と確定済みを分離
        drafts = [s for s in schedules if not s["is_confirmed"]]
        confirmed_list = [s for s in schedules if s["is_confirmed"]]

        # 確定済みスケジュールの表示
        for sched in confirmed_list:
            relaxed = " [緩和あり]" if sched.get("relaxations") else ""
            with st.expander(
                f"{sched['plan_name']} [確定]{relaxed} "
                f"(分散: {sched['total_variance']:.0f}, "
                f"満足度: {sched['satisfaction_score']:.1f})",
                expanded=True
            ):
                if sched.get("relaxations"):
                    st.caption(f"制約緩和: {', '.join(sched['relaxations'])}")
                render_schedule_table(sched, _doctors, _clinics)
                render_doctor_view_table(sched, _doctors)
                render_doctor_stats_table(sched, _doctors, _clinics)

                # 再編集（確定解除）ボタン
                btn_cols = st.columns(3)
                with btn_cols[0]:
                    st.success("確定済み")
                with btn_cols[1]:
                    if st.button("再編集（確定解除）", key=f"unconfirm_{sched['id']}"):
                        st.session_state[f"confirm_unconfirm_{sched['id']}"] = True
                with btn_cols[2]:
                    # 下書きをすべて削除
                    if drafts:
                        if st.button(f"下書きをすべて削除（{len(drafts)}件）",
                                     key=f"del_all_drafts_{sched['id']}", type="secondary"):
                            for d in drafts:
                                delete_schedule(d["id"], year_month=target_month)
                            st.session_state["_toast_msg"] = f"{len(drafts)}件の下書きを削除しました"
                            st.rerun()

                # 確定解除の確認ダイアログ
                if st.session_state.get(f"confirm_unconfirm_{sched['id']}"):
                    st.warning("確定を解除して下書きに戻しますか？医員に公開されているスケジュールが非公開になり、カレンダーイベントも削除されます。")
                    uc1, uc2 = st.columns(2)
                    with uc1:
                        if st.button("確定を解除する", key=f"do_unconfirm_{sched['id']}", type="primary"):
                            unconfirm_schedule(sched["id"], year_month=target_month)
                            _send_calendar_clear(target_month)
                            st.session_state.pop(f"confirm_unconfirm_{sched['id']}", None)
                            st.session_state["_toast_msg"] = "確定を解除しました。下書き編集タブで再編集できます。"
                            st.rerun()
                    with uc2:
                        if st.button("キャンセル", key=f"cancel_unconfirm_{sched['id']}"):
                            st.session_state.pop(f"confirm_unconfirm_{sched['id']}", None)
                            st.rerun()

        # 未確定（下書き候補）スケジュールの表示
        for sched in drafts:
            draft_label = "[下書き]" if _is_saved_as_draft(sched, schedules) else ""
            relaxed = " [緩和あり]" if sched.get("relaxations") else ""
            with st.expander(
                f"{sched['plan_name']} {draft_label}{relaxed} "
                f"(分散: {sched['total_variance']:.0f}, "
                f"満足度: {sched['satisfaction_score']:.1f})",
                expanded=False
            ):
                if sched.get("relaxations"):
                    st.caption(f"制約緩和: {', '.join(sched['relaxations'])}")
                render_schedule_table(sched, _doctors, _clinics)
                render_doctor_view_table(sched, _doctors)

                # △日に割り当てがある場合の警告
                avoid_hits = []
                for a in sched["assignments"]:
                    if a["date"] in avoid_map.get(a["doctor_id"], set()):
                        d_obj = date.fromisoformat(a["date"])
                        avoid_hits.append(
                            f"{doc_name_map.get(a['doctor_id'], '?')} → "
                            f"{d_obj.strftime('%m/%d')} "
                            f"{clinic_name_map.get(a['clinic_id'], '?')}"
                        )
                if avoid_hits:
                    st.warning(
                        f"△（できれば避けたい）日に割り当てがあります（{len(avoid_hits)}件）:\n"
                        + "、".join(avoid_hits)
                    )

                render_doctor_stats_table(sched, _doctors, _clinics)

                # アクションボタン
                btn_cols = st.columns(2)
                with btn_cols[0]:
                    if st.button("下書きとして保存", key=f"gen_save_draft_{sched['id']}",
                                 type="primary"):
                        _save_as_draft(target_month, sched, schedules)
                        st.session_state["_toast_msg"] = "下書きとして保存しました。下書き編集タブで編集できます。"
                        st.rerun()
                with btn_cols[1]:
                    if st.button("削除", key=f"del_{sched['id']}", type="secondary"):
                        st.session_state[f"confirm_del_sched_{sched['id']}"] = True

                # 削除確認
                if st.session_state.get(f"confirm_del_sched_{sched['id']}"):
                    st.warning(f"「{sched['plan_name']}」を削除しますか？")
                    dc1, dc2 = st.columns(2)
                    with dc1:
                        if st.button("削除する", key=f"do_del_{sched['id']}", type="primary"):
                            delete_schedule(sched["id"], year_month=target_month)
                            st.session_state.pop(f"confirm_del_sched_{sched['id']}", None)
                            st.rerun()
                    with dc2:
                        if st.button("キャンセル", key=f"cancel_del_{sched['id']}"):
                            st.session_state.pop(f"confirm_del_sched_{sched['id']}", None)
                            st.rerun()


def _is_saved_as_draft(sched, all_schedules):
    """このスケジュールが下書きとして保存済みかを判定（同月で唯一の未確定ならTrue）"""
    drafts = [s for s in all_schedules if not s["is_confirmed"]]
    return len(drafts) == 1 and drafts[0]["id"] == sched["id"]


def _save_as_draft(target_month, sched, all_schedules):
    """案を下書きとして保存（同月1件のみ、既存下書きは上書き）"""
    existing_drafts = [s for s in all_schedules if not s["is_confirmed"] and s["id"] != sched["id"]]
    # 既存の他の下書きを削除
    for d in existing_drafts:
        delete_schedule(d["id"], year_month=target_month)
    # 選択した案のassignmentsはそのまま保持（既にGoogle Sheetsに保存済み）


def _send_calendar_clear(target_month):
    """GAS Web App経由で確定解除時のカレンダーイベントを削除"""
    gas_url = st.secrets.get("gas_webapp_url", "")
    if not gas_url:
        return
    try:
        requests.post(gas_url, json={
            "action": "schedule_unconfirmed",
            "year_month": target_month,
        }, timeout=10)
    except requests.RequestException:
        st.warning("カレンダーイベントの削除に失敗しました。手動でカレンダーを確認してください。")
