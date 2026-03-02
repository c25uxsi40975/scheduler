"""管理者: スケジュール生成タブ"""
import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import date
from dateutil.relativedelta import relativedelta
from database import (
    get_doctors, get_clinics, get_all_preferences,
    get_affinities, get_schedules, save_schedule, confirm_schedule,
    delete_schedule, update_schedule_assignments,
    get_clinic_date_overrides, get_all_confirmed_schedules,
    delete_old_schedules, append_training_data,
    append_suitability_training_data,
)
from ml_adjuster import (
    compute_doctor_features, FEATURE_COLUMNS, PAIR_FEATURE_COLUMNS,
    _compute_doctor_history, compute_pair_features,
)
from optimizer import get_target_saturdays, get_clinic_dates, PRIORITY_MANDATORY, PRIORITY_EXCLUDED, diagnose_infeasibility
from pipeline import run_integrated_pipeline
from components.schedule_table import render_schedule_table, render_doctor_view_table, render_doctor_stats_table
from components.display_utils import build_display_name_map, build_reverse_display_name_map


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
    """確定スケジュールからペア適合性学習データを計算してGoogle Sheetsに追記。

    ポジティブサンプル: 割り当てられた (doctor, clinic, date) ペア → 割当結果=1
    ネガティブサンプル: 利用可能だが割り当てられなかった ペア → 割当結果=0
    """
    # 優先度マスタを辞書化
    affinities_by_doctor = {}
    for a in affinities:
        affinities_by_doctor.setdefault(a["doctor_id"], {})[a["clinic_id"]] = a["weight"]

    # NG日マップ（希望データから取得）
    prefs = get_all_preferences(target_month)
    ng_map = {}
    for p in prefs:
        ng_map[p["doctor_id"]] = set(p.get("ng_dates", []))

    # 医員の履歴を事前計算
    doctor_histories = {}
    for doc in doctors:
        doctor_histories[doc["id"]] = _compute_doctor_history(
            doc, clinics, confirmed_schedules, target_month
        )

    # 割当済みペアを (doctor_id, clinic_id, date_str) のセットに
    positive_set = set()
    for a in sched["assignments"]:
        positive_set.add((a["doctor_id"], a["clinic_id"], a["date"]))

    # 日付ごとにアクティブな外勤先を特定
    active_clinics_by_date = {}
    for a in sched["assignments"]:
        active_clinics_by_date.setdefault(a["date"], set()).add(a["clinic_id"])

    rows = []
    for date_str in sorted(active_clinics_by_date.keys()):
        active_cids = active_clinics_by_date[date_str]

        for doc in doctors:
            # NG日の医員はスキップ（利用不可なのでサンプルに含めない）
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
                result = run_integrated_pipeline(
                    target_month, year, month,
                    doctors, clinics, confirmed, prefs, affinities,
                    overrides, previous_earnings=previous_earnings,
                )
                plans = result["plans"]

            if not plans:
                st.error("制約を満たすスケジュールが見つかりません。制約条件を見直してください。")
                diag = diagnose_infeasibility(
                    doctors, clinics, saturdays, prefs, affinities,
                    date_overrides=overrides,
                )
                with st.expander("診断情報", expanded=True):
                    for line in diag:
                        st.write(f"- {line}")
            else:
                # 未確定の旧案を削除してから新案を保存
                old_schedules = get_schedules(target_month)
                for old in old_schedules:
                    if not old["is_confirmed"]:
                        delete_schedule(old["id"])

                for plan in plans:
                    save_schedule(
                        target_month,
                        plan["plan_name"],
                        plan["assignments"],
                        plan["total_variance"],
                        plan["satisfaction_score"]
                    )

                st.success(f"{len(plans)}件の案を生成しました")
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

        for sched in schedules:
            confirmed = "[確定]" if sched["is_confirmed"] else ""
            relaxed = " [緩和あり]" if sched.get("relaxations") else ""
            with st.expander(
                f"{sched['plan_name']} {confirmed}{relaxed} "
                f"(分散: {sched['total_variance']:.0f}, "
                f"満足度: {sched['satisfaction_score']:.1f})",
                expanded=sched["is_confirmed"]
            ):
                if sched.get("relaxations"):
                    st.caption(f"制約緩和: {', '.join(sched['relaxations'])}")
                # 手動調整モード
                editing_key = f"editing_sched_{sched['id']}"
                is_editing = st.session_state.get(editing_key, False)

                if is_editing:
                    _render_edit_mode(sched, _doctors, clinic_map, editing_key, prefs, affinities)
                else:
                    render_schedule_table(sched, _doctors, _clinics)

                    # 医員別ビュー（医員 × 日付 → 外勤先）
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

                    # 医員別統計
                    render_doctor_stats_table(sched, _doctors, _clinics)

                    # アクションボタン
                    btn_cols = st.columns(3)
                    with btn_cols[0]:
                        if not sched["is_confirmed"]:
                            if st.button("確定する", key=f"confirm_{sched['id']}",
                                         type="primary"):
                                confirm_schedule(sched["id"])
                                delete_old_schedules(months_to_keep=4)
                                all_confirmed = get_all_confirmed_schedules()
                                _append_training_rows(
                                    target_month, sched, _doctors, _clinics,
                                    all_confirmed,
                                )
                                _append_suitability_training_rows(
                                    target_month, sched, _doctors, _clinics,
                                    all_confirmed, get_affinities(),
                                    get_target_saturdays(year, month),
                                )
                                _send_confirmation_notification(target_month, sched)
                                # 確定後は次の月をデフォルト表示にする（widget keyは直接設定不可なので間接キーを使用）
                                next_month = (date(year, month, 1) + relativedelta(months=1)).strftime("%Y-%m")
                                st.session_state["_pending_target_month"] = next_month
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

    # 限定メンバー（WL）: 外勤先マスタの fixed_doctors から構築
    fixed_members = {}
    for cid, c in clinic_map.items():
        fd = c.get("fixed_doctors") or []
        if fd:
            fixed_members[cid] = set(fd)

    max_assignments_map = {d["id"]: d.get("max_assignments", 0) for d in doctors}
    clinic_time_slot = {cid: c.get("time_slot", "") for cid, c in clinic_map.items()}

    return {
        "ng_map": ng_map,
        "avoid_map": avoid_map,
        "excluded_pairs": excluded_pairs,
        "fixed_members": fixed_members,
        "max_assignments": max_assignments_map,
        "date_clinic_requests": date_clinic_req_map,
        "post_night_map": post_night_map,
        "clinic_time_slot": clinic_time_slot,
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

    for ds in dates:
        d_obj = date.fromisoformat(ds)
        day_label = d_obj.strftime("%m/%d(%a)")
        day_doctors = []

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

            # ハード制約チェック
            if ds in constraints["ng_map"].get(did, set()):
                errors.append(f"{day_label} {cname}: {dname} はNG日です")
            if (did, cid) in constraints["excluded_pairs"]:
                errors.append(f"{day_label} {cname}: {dname} は除外対象です")
            if ds in constraints["post_night_map"].get(did, set()):
                if constraints["clinic_time_slot"].get(cid, "") != "PM":
                    errors.append(f"{day_label} {cname}: {dname} は当直明けのためPM以外不可")

            # 同日重複チェック
            if did in day_doctors:
                errors.append(f"{day_label}: {dname} が同日に複数割り当てされています")
            day_doctors.append(did)

            new_assignments.append({"date": ds, "clinic_id": cid, "doctor_id": did})

    return new_assignments, errors


def _render_edit_mode(sched, doctors, clinic_map, editing_key, prefs, affinities):
    """スケジュールの手動調整UI（マトリクス形式）"""
    st.info("手動調整モード: マトリクスのセルを直接編集してください")

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
        key=f"edit_matrix_{sched['id']}",
    )

    confirm_save_key = f"confirm_save_warnings_{sched['id']}"

    btn_cols = st.columns(2)
    with btn_cols[0]:
        if st.button("変更を保存", key=f"save_edit_{sched['id']}", type="primary"):
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
                    }
                    st.rerun()
                else:
                    update_schedule_assignments(sched["id"], new_assignments)
                    st.session_state.pop(editing_key, None)
                    st.success("保存しました")
                    st.rerun()
    with btn_cols[1]:
        if st.button("キャンセル", key=f"cancel_edit_{sched['id']}"):
            st.session_state.pop(editing_key, None)
            st.session_state.pop(confirm_save_key, None)
            st.rerun()

    # ソフト制約違反の確認ダイアログ
    saved_confirm = st.session_state.get(confirm_save_key)
    if saved_confirm:
        st.markdown("---")
        st.warning("以下の希望・制約に合致しない変更があります。このまま保存しますか？")
        for w in saved_confirm["warnings"]:
            st.write(f"- {w}")
        wc1, wc2 = st.columns(2)
        with wc1:
            if st.button("確認して保存", key=f"force_save_{sched['id']}", type="primary"):
                update_schedule_assignments(sched["id"], saved_confirm["assignments"])
                st.session_state.pop(editing_key, None)
                st.session_state.pop(confirm_save_key, None)
                st.success("保存しました")
                st.rerun()
        with wc2:
            if st.button("編集に戻る", key=f"back_edit_{sched['id']}"):
                st.session_state.pop(confirm_save_key, None)
                st.rerun()
