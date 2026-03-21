"""管理者: 下書き編集タブ"""
import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import date
from dateutil.relativedelta import relativedelta
from database import (
    get_doctors, get_clinics, get_all_preferences,
    get_affinities, get_schedules, confirm_schedule,
    delete_schedule, update_schedule_assignments,
    get_all_confirmed_schedules, delete_old_schedules,
    append_training_data, append_suitability_training_data,
    get_double_shift_pairs,
)
from ml_adjuster import (
    compute_doctor_features, FEATURE_COLUMNS, PAIR_FEATURE_COLUMNS,
    _compute_doctor_history, compute_pair_features,
)
from optimizer import get_target_saturdays, PRIORITY_EXCLUDED
from components.schedule_table import render_schedule_table, render_doctor_view_table, render_doctor_stats_table
from components.display_utils import build_display_name_map, build_reverse_display_name_map


def render(target_month, year, month):
    if not st.session_state.get("admin_authenticated"):
        st.stop()
    st.header(f"下書き編集 ({target_month})")

    schedules = get_schedules(target_month)
    drafts = [s for s in schedules if not s["is_confirmed"]]

    if not drafts:
        st.info("下書きがありません。スケジュール生成タブで案を下書きとして保存してください。")
        return

    # 下書きは同月1件を想定（複数ある場合は最新を使用）
    sched = drafts[0]

    doctors = get_doctors()
    clinics = get_clinics()
    prefs = get_all_preferences(target_month)
    affinities = get_affinities()

    clinic_map = {c["id"]: c for c in clinics}

    st.caption(f"下書き: {sched['plan_name']} (分散: {sched['total_variance']:.0f}, 満足度: {sched['satisfaction_score']:.1f})")

    # 手動編集UI
    _render_edit_mode(sched, doctors, clinic_map, prefs, affinities, target_month, year, month, clinics)


def _render_edit_mode(sched, doctors, clinic_map, prefs, affinities, target_month, year, month, clinics):
    """スケジュールの手動調整UI（マトリクス形式）"""
    st.info("マトリクスのセルを直接編集してください")

    constraints = _build_constraint_data(doctors, prefs, affinities, clinic_map)
    assignments = sched["assignments"]

    # 名前⇔IDマップ
    doc_id_to_name = build_display_name_map(doctors)
    doc_name_to_id = build_reverse_display_name_map(doctors)
    clinic_id_to_name = {cid: c["name"] for cid, c in clinic_map.items()}

    # スケジュールの日付と外勤先を抽出
    dates = sorted(set(a["date"] for a in assignments))
    clinics_in_sched = sorted(
        set(a["clinic_id"] for a in assignments),
        key=lambda cid: clinic_map.get(cid, {}).get("name", "")
    )

    # assignments → DataFrame（名前ベース）
    slot_map = {}
    for a in assignments:
        slot_map[(a["date"], a["clinic_id"])] = a["doctor_id"]

    all_doc_names = [""] + [doc_id_to_name[d["id"]] for d in doctors]

    rows = []
    for ds in dates:
        d_obj = date.fromisoformat(ds)
        row = {"日付": d_obj.strftime("%m/%d(%a)")}
        for cid in clinics_in_sched:
            cname = clinic_id_to_name.get(cid, "?")
            did = slot_map.get((ds, cid), "")
            row[cname] = doc_id_to_name.get(did, "") if did else ""
        rows.append(row)

    df = pd.DataFrame(rows).set_index("日付")

    # カラム設定: 外勤先ごとのSelectboxColumn
    col_config = {}
    for cid in clinics_in_sched:
        cname = clinic_id_to_name.get(cid, "?")
        fixed = constraints["fixed_members"].get(cid, set())
        if fixed:
            options = [""] + [doc_id_to_name[did] for did in fixed if did in doc_id_to_name]
        else:
            options = all_doc_names
        col_config[cname] = st.column_config.SelectboxColumn(
            cname, options=options, required=True, width="small",
        )

    edited_df = st.data_editor(
        df, column_config=col_config, use_container_width=True,
        key=f"draft_edit_matrix_{sched['id']}",
    )

    confirm_save_key = f"confirm_save_warnings_draft_{sched['id']}"

    btn_cols = st.columns(3)
    with btn_cols[0]:
        if st.button("下書き保存", key=f"save_draft_{sched['id']}", type="primary"):
            new_assignments, hard_errors = _validate_and_convert(
                edited_df, dates, clinics_in_sched,
                doc_name_to_id, clinic_id_to_name, constraints,
            )
            if hard_errors:
                for e in hard_errors:
                    st.error(e)
            else:
                soft_warnings = _check_soft_constraints(new_assignments, constraints, doctors)
                if soft_warnings:
                    st.session_state[confirm_save_key] = {
                        "warnings": soft_warnings,
                        "assignments": new_assignments,
                        "action": "save",
                    }
                    st.rerun()
                else:
                    update_schedule_assignments(sched["id"], new_assignments)
                    st.session_state["_toast_msg"] = "下書きを保存しました"
                    st.rerun()
    with btn_cols[1]:
        if st.button("確定する", key=f"confirm_draft_{sched['id']}"):
            new_assignments, hard_errors = _validate_and_convert(
                edited_df, dates, clinics_in_sched,
                doc_name_to_id, clinic_id_to_name, constraints,
            )
            if hard_errors:
                for e in hard_errors:
                    st.error(e)
            else:
                soft_warnings = _check_soft_constraints(new_assignments, constraints, doctors)
                if soft_warnings:
                    st.session_state[confirm_save_key] = {
                        "warnings": soft_warnings,
                        "assignments": new_assignments,
                        "action": "confirm",
                    }
                    st.rerun()
                else:
                    _do_confirm(sched, new_assignments, target_month, year, month, doctors, clinics, affinities)
    with btn_cols[2]:
        if st.button("下書き削除", key=f"del_draft_{sched['id']}", type="secondary"):
            st.session_state[f"confirm_del_draft_{sched['id']}"] = True

    # 下書き削除確認
    if st.session_state.get(f"confirm_del_draft_{sched['id']}"):
        st.warning(f"下書き「{sched['plan_name']}」を削除しますか？")
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("削除する", key=f"do_del_draft_{sched['id']}", type="primary"):
                delete_schedule(sched["id"])
                st.session_state.pop(f"confirm_del_draft_{sched['id']}", None)
                st.session_state["_toast_msg"] = "下書きを削除しました"
                st.rerun()
        with dc2:
            if st.button("キャンセル", key=f"cancel_del_draft_{sched['id']}"):
                st.session_state.pop(f"confirm_del_draft_{sched['id']}", None)
                st.rerun()

    # ソフト制約違反の確認ダイアログ
    saved_confirm = st.session_state.get(confirm_save_key)
    if saved_confirm:
        st.markdown("---")
        st.warning("以下の希望・制約に合致しない変更があります。このまま保存しますか？")
        for w in saved_confirm["warnings"]:
            st.write(f"- {w}")
        wc1, wc2 = st.columns(2)
        action = saved_confirm["action"]
        with wc1:
            label = "確認して確定" if action == "confirm" else "確認して保存"
            if st.button(label, key=f"force_{action}_draft_{sched['id']}", type="primary"):
                if action == "confirm":
                    _do_confirm(sched, saved_confirm["assignments"], target_month, year, month, doctors, clinics, affinities)
                else:
                    update_schedule_assignments(sched["id"], saved_confirm["assignments"])
                    st.session_state.pop(confirm_save_key, None)
                    st.session_state["_toast_msg"] = "下書きを保存しました"
                    st.rerun()
        with wc2:
            if st.button("編集に戻る", key=f"back_edit_draft_{sched['id']}"):
                st.session_state.pop(confirm_save_key, None)
                st.rerun()

    # 現在のスケジュール表示（参照用）
    st.markdown("---")
    with st.expander("現在の下書き内容（参照用）", expanded=False):
        render_schedule_table(sched, doctors, list(clinic_map.values()))
        render_doctor_view_table(sched, doctors)
        render_doctor_stats_table(sched, doctors, list(clinic_map.values()))


def _do_confirm(sched, new_assignments, target_month, year, month, doctors, clinics, affinities):
    """下書きを確定する（assignments更新 + 確定 + 学習データ + 通知）"""
    # まずassignmentsを保存
    update_schedule_assignments(sched["id"], new_assignments)
    # 確定
    confirm_schedule(sched["id"])
    delete_old_schedules(months_to_keep=4)
    # 学習データ追記
    all_confirmed = get_all_confirmed_schedules()
    # 確定後のschedを再構築（assignmentsを更新済みのものに差し替え）
    confirmed_sched = dict(sched)
    confirmed_sched["assignments"] = new_assignments
    _append_training_rows(target_month, confirmed_sched, doctors, clinics, all_confirmed)
    _append_suitability_training_rows(
        target_month, confirmed_sched, doctors, clinics,
        all_confirmed, affinities, get_target_saturdays(year, month),
    )
    _send_confirmation_notification(target_month, confirmed_sched)
    # 確定後は次の月をデフォルト表示にする
    next_month = (date(year, month, 1) + relativedelta(months=1)).strftime("%Y-%m")
    st.session_state["_pending_target_month"] = next_month
    st.session_state["_toast_msg"] = "確定しました！"
    st.rerun()


def _append_training_rows(target_month, sched, doctors, clinics, confirmed_schedules):
    """確定スケジュールから学習データを計算してGoogle Sheetsに追記"""
    effort_map = {c["id"]: c.get("effort_cost", 0) for c in clinics}

    doc_assignments = {}
    for a in sched["assignments"]:
        doc_assignments.setdefault(a["doctor_id"], []).append(
            (a["date"], a["clinic_id"])
        )

    rows = []
    for doc in doctors:
        if doc["id"] not in doc_assignments:
            continue
        features = compute_doctor_features(
            doc, clinics, confirmed_schedules, target_month
        )
        for a_date, clinic_id in doc_assignments[doc["id"]]:
            row = [
                str(doc["id"]),
                target_month,
                a_date,
            ]
            for col in FEATURE_COLUMNS:
                val = features.get(col, "")
                row.append("" if (isinstance(val, float) and np.isnan(val)) else val)
            row.append(effort_map.get(clinic_id, 0))
            rows.append(row)

    if rows:
        append_training_data(rows)


def _append_suitability_training_rows(target_month, sched, doctors, clinics,
                                       confirmed_schedules, affinities, saturdays):
    """確定スケジュールからペア適合性学習データを計算してGoogle Sheetsに追記"""
    affinities_by_doctor = {}
    for a in affinities:
        affinities_by_doctor.setdefault(a["doctor_id"], {})[a["clinic_id"]] = a["weight"]

    prefs = get_all_preferences(target_month)
    ng_map = {}
    for p in prefs:
        ng_map[p["doctor_id"]] = set(p.get("ng_dates", []))

    doctor_histories = {}
    for doc in doctors:
        doctor_histories[doc["id"]] = _compute_doctor_history(
            doc, clinics, confirmed_schedules, target_month
        )

    positive_set = set()
    for a in sched["assignments"]:
        positive_set.add((a["doctor_id"], a["clinic_id"], a["date"]))

    active_clinics_by_date = {}
    for a in sched["assignments"]:
        active_clinics_by_date.setdefault(a["date"], set()).add(a["clinic_id"])

    rows = []
    for date_str in sorted(active_clinics_by_date.keys()):
        active_cids = active_clinics_by_date[date_str]

        for doc in doctors:
            if date_str in ng_map.get(doc["id"], set()):
                continue

            dh = doctor_histories[doc["id"]]
            aff_map = affinities_by_doctor.get(doc["id"], {})

            for clinic in clinics:
                if clinic["id"] not in active_cids:
                    continue

                features = compute_pair_features(dh, clinic, aff_map)
                assigned = 1 if (doc["id"], clinic["id"], date_str) in positive_set else 0

                row = [
                    str(doc["id"]),
                    str(clinic["id"]),
                    target_month,
                    date_str,
                ]
                for col in PAIR_FEATURE_COLUMNS:
                    val = features.get(col, "")
                    row.append("" if (isinstance(val, float) and np.isnan(val)) else val)
                row.append(assigned)
                rows.append(row)

    if rows:
        append_suitability_training_data(rows)


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


def _build_constraint_data(doctors, prefs, affinities, clinic_map):
    """制約チェック用のルックアップデータを構築"""
    ng_map = {}
    avoid_map = {}
    date_clinic_req_map = {}
    post_night_map = {}
    for p in prefs:
        did = p["doctor_id"]
        ng_map[did] = set(p.get("ng_dates") or [])
        avoid_map[did] = set(p.get("avoid_dates") or [])
        dcr = p.get("date_clinic_requests") or {}
        if dcr:
            date_clinic_req_map[did] = dcr
        pn = set(p.get("post_night_dates") or [])
        if pn:
            post_night_map[did] = pn

    excluded_pairs = set()
    for a in affinities:
        if a["weight"] == PRIORITY_EXCLUDED:
            excluded_pairs.add((a["doctor_id"], a["clinic_id"]))

    fixed_members = {}
    for cid, c in clinic_map.items():
        fd = c.get("fixed_doctors") or []
        if fd:
            fixed_members[cid] = set(fd)

    max_assignments_map = {d["id"]: d.get("max_assignments", 0) for d in doctors}
    clinic_time_slot = {cid: c.get("time_slot", "") for cid, c in clinic_map.items()}

    ds_pairs = get_double_shift_pairs(active_only=True)
    ds_pair_set = {(p["am_clinic_id"], p["pm_clinic_id"]) for p in ds_pairs}

    return {
        "ng_map": ng_map,
        "avoid_map": avoid_map,
        "excluded_pairs": excluded_pairs,
        "fixed_members": fixed_members,
        "max_assignments": max_assignments_map,
        "date_clinic_requests": date_clinic_req_map,
        "post_night_map": post_night_map,
        "clinic_time_slot": clinic_time_slot,
        "double_shift_pairs_set": ds_pair_set,
    }


def _check_soft_constraints(new_assignments, constraints, doctors):
    """ソフト制約違反の警告メッセージリストを返す"""
    doc_name_map = build_display_name_map(doctors)
    avoid_map = constraints["avoid_map"]
    max_assignments_map = constraints["max_assignments"]
    date_clinic_req_map = constraints["date_clinic_requests"]

    warnings = []

    for a in new_assignments:
        did, ds, cid = a["doctor_id"], a["date"], a["clinic_id"]
        dname = doc_name_map.get(did, "?")
        if ds in avoid_map.get(did, set()):
            d_obj = date.fromisoformat(ds)
            warnings.append(
                f"{dname} は {d_obj.strftime('%m/%d')} を「できれば避けたい」に設定しています"
            )
        requested_cid = date_clinic_req_map.get(did, {}).get(ds)
        if requested_cid is not None and int(requested_cid) != cid:
            d_obj = date.fromisoformat(ds)
            warnings.append(
                f"{dname} は {d_obj.strftime('%m/%d')} に別の外勤先を希望しています"
            )

    doc_counts = {}
    for a in new_assignments:
        doc_counts[a["doctor_id"]] = doc_counts.get(a["doctor_id"], 0) + 1
    for did, count in doc_counts.items():
        max_a = max_assignments_map.get(did, 0)
        if max_a > 0 and count > max_a:
            dname = doc_name_map.get(did, "?")
            warnings.append(f"{dname} の割当 {count}回 が月上限 {max_a}回 を超えています")

    return warnings


def _validate_and_convert(edited_df, dates, clinics_in_sched,
                          doc_name_to_id, clinic_id_to_name, constraints):
    """編集後DataFrameを assignments に変換 + ハード制約チェック"""
    new_assignments = []
    errors = []

    ds_pair_set = constraints.get("double_shift_pairs_set", set())

    for ds in dates:
        d_obj = date.fromisoformat(ds)
        day_label = d_obj.strftime("%m/%d(%a)")
        day_doctor_clinics = {}

        for cid in clinics_in_sched:
            cname = clinic_id_to_name.get(cid, "?")
            cell = edited_df.at[day_label, cname]
            dname = cell if cell and str(cell).strip() else ""
            if not dname:
                continue
            did = doc_name_to_id.get(dname)
            if not did:
                errors.append(f"{day_label} {cname}: 不明な医員「{dname}」")
                continue

            if ds in constraints["ng_map"].get(did, set()):
                errors.append(f"{day_label} {cname}: {dname} はNG日です")
            if (did, cid) in constraints["excluded_pairs"]:
                errors.append(f"{day_label} {cname}: {dname} は除外対象です")
            if ds in constraints["post_night_map"].get(did, set()):
                if constraints["clinic_time_slot"].get(cid, "") != "PM":
                    errors.append(f"{day_label} {cname}: {dname} は当直明けのためPM以外不可")

            prev_cids = day_doctor_clinics.get(did, [])
            if prev_cids:
                if len(prev_cids) >= 2:
                    errors.append(f"{day_label}: {dname} が同日に3件以上割り当てされています")
                else:
                    prev_cid = prev_cids[0]
                    is_valid_pair = (
                        (prev_cid, cid) in ds_pair_set
                        or (cid, prev_cid) in ds_pair_set
                    )
                    if not is_valid_pair:
                        errors.append(
                            f"{day_label}: {dname} が同日に複数割り当て"
                            "（掛け持ちペア未登録の組み合わせです）"
                        )
            day_doctor_clinics.setdefault(did, []).append(cid)

            new_assignments.append({"date": ds, "clinic_id": cid, "doctor_id": did})

    return new_assignments, errors
