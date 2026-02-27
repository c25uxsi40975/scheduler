"""管理者: スケジュール生成タブ"""
import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import date
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
from optimizer import get_target_saturdays, get_clinic_dates, PRIORITY_FIXED, PRIORITY_EXCLUDED
from pipeline import run_integrated_pipeline
from components.schedule_table import render_schedule_table


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
    fixed_members = {}
    for a in affinities:
        if a["weight"] == PRIORITY_EXCLUDED:
            excluded_pairs.add((a["doctor_id"], a["clinic_id"]))
        elif a["weight"] == PRIORITY_FIXED:
            fixed_members.setdefault(a["clinic_id"], set()).add(a["doctor_id"])

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


def _get_allowed_doctors(date_str, clinic_id, doctor_options, constraints, same_day_others):
    """ハード制約に基づいてselectboxの選択肢をフィルタリング"""
    ng_map = constraints["ng_map"]
    excluded_pairs = constraints["excluded_pairs"]
    fixed_members = constraints["fixed_members"]
    post_night_map = constraints["post_night_map"]
    clinic_ts = constraints["clinic_time_slot"]

    allowed = []
    for (did, dname) in doctor_options:
        if did == "":
            allowed.append((did, dname))
            continue
        if date_str in ng_map.get(did, set()):
            continue
        if (did, clinic_id) in excluded_pairs:
            continue
        fixed = fixed_members.get(clinic_id, set())
        if fixed and did not in fixed:
            continue
        if did in same_day_others:
            continue
        # 当直明け日はPM以外の外勤先に割り当て不可
        if date_str in post_night_map.get(did, set()) and clinic_ts.get(clinic_id, "") != "PM":
            continue
        allowed.append((did, dname))
    return allowed


def _check_soft_constraints(new_assignments, constraints, doctors):
    """ソフト制約違反の警告メッセージリストを返す"""
    doc_name_map = {d["id"]: d["name"] for d in doctors}
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


def _render_edit_mode(sched, doctors, clinic_map, editing_key, prefs, affinities):
    """スケジュールの手動調整UI（制約チェック付き）"""
    st.info("手動調整モード: 各スロットの担当医員を変更できます")

    constraints = _build_constraint_data(doctors, prefs, affinities, clinic_map)
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

    # session_stateから同日重複チェック用の現在選択値を収集
    current_selections = {}
    for ds in dates:
        for cid in clinics_in_sched:
            if (ds, cid) not in slot_map:
                continue
            key = f"slot_{sched['id']}_{ds}_{cid}"
            val = st.session_state.get(key)
            if val and val[0]:
                current_selections[(ds, cid)] = val[0]
            else:
                current_selections[(ds, cid)] = slot_map.get((ds, cid), "")

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
                # 同日の他スロットで選択済みの医員を取得
                same_day_others = {
                    did for (ds2, cid2), did in current_selections.items()
                    if ds2 == ds and cid2 != cid and did
                }
                # ハード制約でフィルタリングした選択肢
                allowed = _get_allowed_doctors(
                    ds, cid, doctor_options, constraints, same_day_others
                )

                # 現在の担当医員のインデックスを取得
                current_idx = 0
                for j, (did, _) in enumerate(allowed):
                    if did == current_did:
                        current_idx = j
                        break

                # 元の割当がハード制約違反で選択肢にない場合の警告
                if current_did and current_did not in [did for did, _ in allowed]:
                    invalid_name = next(
                        (d["name"] for d in doctors if d["id"] == current_did), "?"
                    )
                    st.caption(f"元の担当 {invalid_name} はハード制約違反のため選択不可")

                selected = st.selectbox(
                    cname,
                    allowed,
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

    confirm_save_key = f"confirm_save_warnings_{sched['id']}"

    btn_cols = st.columns(2)
    with btn_cols[0]:
        if st.button("変更を保存", key=f"save_edit_{sched['id']}", type="primary"):
            soft_warnings = _check_soft_constraints(new_assignments, constraints, doctors)
            if soft_warnings:
                st.session_state[confirm_save_key] = soft_warnings
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
    if st.session_state.get(confirm_save_key):
        st.markdown("---")
        st.warning("以下の希望・制約に合致しない変更があります。このまま保存しますか？")
        for w in st.session_state[confirm_save_key]:
            st.write(f"- {w}")
        wc1, wc2 = st.columns(2)
        with wc1:
            if st.button("確認して保存", key=f"force_save_{sched['id']}", type="primary"):
                update_schedule_assignments(sched["id"], new_assignments)
                st.session_state.pop(editing_key, None)
                st.session_state.pop(confirm_save_key, None)
                st.success("保存しました")
                st.rerun()
        with wc2:
            if st.button("編集に戻る", key=f"back_edit_{sched['id']}"):
                st.session_state.pop(confirm_save_key, None)
                st.rerun()
