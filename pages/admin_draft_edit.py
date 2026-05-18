"""管理者: 下書き編集タブ"""
import streamlit as st
import numpy as np
import requests
from datetime import date
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
from components.display_utils import build_display_name_map


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

    # 保存直後のデータを優先（キャッシュ/API遅延対策）
    # NOTE: キーに target_month を含めて月間のID衝突を防止
    saved_key = f"_draft_saved_assignments_{target_month}_{sched['id']}"
    if saved_key in st.session_state:
        sched = dict(sched)
        sched["assignments"] = st.session_state.pop(saved_key)

    doctors = get_doctors()
    clinics = get_clinics()
    prefs = get_all_preferences(target_month)
    affinities = get_affinities()

    clinic_map = {c["id"]: c for c in clinics}

    st.caption(f"下書き: {sched['plan_name']} (分散: {sched['total_variance']:.0f}, 満足度: {sched['satisfaction_score']:.1f})")

    # 手動編集UI
    _render_edit_mode(sched, doctors, clinic_map, prefs, affinities, target_month, year, month, clinics)


def _render_edit_mode(sched, doctors, clinic_map, prefs, affinities, target_month, year, month, clinics):
    """スケジュールの手動調整UI（セル単位のSelectbox格子）"""
    st.info("セルをクリックして担当医員を変更してください。⛔ はNG日、⚠ は『できれば避けたい』日の医員です。")

    constraints = _build_constraint_data(doctors, prefs, affinities, clinic_map)
    assignments = sched["assignments"]

    doc_id_to_name = build_display_name_map(doctors)
    clinic_id_to_name = {cid: c["name"] for cid, c in clinic_map.items()}

    ng_map = constraints["ng_map"]
    avoid_map = constraints["avoid_map"]

    dates = sorted(set(a["date"] for a in assignments))
    clinics_in_sched = sorted(
        set(a["clinic_id"] for a in assignments),
        key=lambda cid: clinic_map.get(cid, {}).get("name", "")
    )

    slot_map = {}
    for a in assignments:
        slot_map[(a["date"], a["clinic_id"])] = a["doctor_id"]

    edit_ver = st.session_state.get(f"_draft_edit_ver_{target_month}_{sched['id']}", 0)

    edited_slots = _render_selectbox_grid(
        dates, clinics_in_sched, slot_map, doctors, doc_id_to_name,
        clinic_id_to_name, constraints, target_month, sched["id"], edit_ver,
    )

    _render_live_warnings(edited_slots, constraints, doc_id_to_name, clinic_id_to_name)

    confirm_save_key = f"confirm_save_warnings_draft_{target_month}_{sched['id']}"

    btn_cols = st.columns(3)
    with btn_cols[0]:
        if st.button("下書き保存", key=f"save_draft_{target_month}_{sched['id']}", type="primary"):
            new_assignments, hard_errors = _validate_and_convert(
                edited_slots, dates, clinics_in_sched,
                doc_id_to_name, clinic_id_to_name, constraints,
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
                    update_schedule_assignments(sched["id"], new_assignments, year_month=target_month)
                    st.session_state[f"_draft_edit_ver_{target_month}_{sched['id']}"] = edit_ver + 1
                    st.session_state[f"_draft_saved_assignments_{target_month}_{sched['id']}"] = new_assignments
                    st.session_state["_toast_msg"] = "下書きを保存しました"
                    st.rerun()
    with btn_cols[1]:
        if st.button("確定する", key=f"confirm_draft_{target_month}_{sched['id']}"):
            new_assignments, hard_errors = _validate_and_convert(
                edited_slots, dates, clinics_in_sched,
                doc_id_to_name, clinic_id_to_name, constraints,
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
        if st.button("下書き削除", key=f"del_draft_{target_month}_{sched['id']}", type="secondary"):
            st.session_state[f"confirm_del_draft_{target_month}_{sched['id']}"] = True

    # 下書き削除確認
    if st.session_state.get(f"confirm_del_draft_{target_month}_{sched['id']}"):
        st.warning(f"下書き「{sched['plan_name']}」を削除しますか？")
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("削除する", key=f"do_del_draft_{target_month}_{sched['id']}", type="primary"):
                delete_schedule(sched["id"], year_month=target_month)
                st.session_state.pop(f"confirm_del_draft_{target_month}_{sched['id']}", None)
                st.session_state["_toast_msg"] = "下書きを削除しました"
                st.rerun()
        with dc2:
            if st.button("キャンセル", key=f"cancel_del_draft_{target_month}_{sched['id']}"):
                st.session_state.pop(f"confirm_del_draft_{target_month}_{sched['id']}", None)
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
            if st.button(label, key=f"force_{action}_draft_{target_month}_{sched['id']}", type="primary"):
                if action == "confirm":
                    st.session_state.pop(confirm_save_key, None)
                    _do_confirm(sched, saved_confirm["assignments"], target_month, year, month, doctors, clinics, affinities)
                else:
                    update_schedule_assignments(sched["id"], saved_confirm["assignments"], year_month=target_month)
                    st.session_state.pop(confirm_save_key, None)
                    st.session_state[f"_draft_edit_ver_{target_month}_{sched['id']}"] = edit_ver + 1
                    st.session_state[f"_draft_saved_assignments_{target_month}_{sched['id']}"] = saved_confirm["assignments"]
                    st.session_state["_toast_msg"] = "下書きを保存しました"
                    st.rerun()
        with wc2:
            if st.button("編集に戻る", key=f"back_edit_draft_{target_month}_{sched['id']}"):
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
    import logging
    _log = logging.getLogger(__name__)
    _log.info("[確定開始] schedule_id=%s, target_month=%s", sched["id"], target_month)

    # まずassignmentsを保存
    update_schedule_assignments(sched["id"], new_assignments, year_month=target_month)
    _log.info("[確定] assignments保存完了")

    # 確定
    confirm_schedule(sched["id"], year_month=target_month)
    _log.info("[確定] confirm_schedule完了")

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
    # スケジュール画像を生成してDriveにアップロード
    schedule_image_file_id = None
    try:
        from components.schedule_image import generate_schedule_image
        from database.drive_utils import upload_schedule_image
        png_bytes = generate_schedule_image(confirmed_sched, doctors, clinics, target_month)
        if png_bytes:
            schedule_image_file_id = upload_schedule_image(
                png_bytes, f"schedule_{target_month}.png"
            )
    except Exception:
        _log.warning("スケジュール画像のアップロードに失敗", exc_info=True)

    _send_confirmation_notification(target_month, confirmed_sched, schedule_image_file_id)
    # 確定後もそのまま同月表示（確認タブで確認できるようにする）
    st.session_state["_toast_msg"] = "確定しました！ スケジュール確認タブで確認してください。"
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


def _send_confirmation_notification(target_month, sched, schedule_image_file_id=None):
    """GAS Web App経由で確定通知メール＋LINE通知を送信"""
    gas_url = st.secrets.get("gas_webapp_url", "")
    if not gas_url:
        return
    try:
        payload = {
            "action": "schedule_confirmed",
            "year_month": target_month,
            "plan_name": sched["plan_name"],
        }
        if schedule_image_file_id:
            payload["schedule_image_file_id"] = schedule_image_file_id
        requests.post(gas_url, json=payload, timeout=10)
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


def _render_selectbox_grid(dates, clinics_in_sched, slot_map, doctors,
                            doc_id_to_name, clinic_id_to_name, constraints,
                            target_month, sched_id, edit_ver):
    """セル単位のSelectbox格子を描画し、編集後の (date, clinic_id) → doctor_id マップを返す"""
    ng_map = constraints["ng_map"]
    avoid_map = constraints["avoid_map"]

    # ヘッダー行
    header_cols = st.columns([1.2] + [2] * len(clinics_in_sched))
    header_cols[0].markdown("**日付**")
    for i, cid in enumerate(clinics_in_sched):
        header_cols[i + 1].markdown(f"**{clinic_id_to_name.get(cid, '?')}**")

    edited_slots = {}
    for ds in dates:
        d_obj = date.fromisoformat(ds)
        label = d_obj.strftime("%m/%d(%a)")
        row_cols = st.columns([1.2] + [2] * len(clinics_in_sched))
        row_cols[0].markdown(label)

        for i, cid in enumerate(clinics_in_sched):
            fixed = constraints["fixed_members"].get(cid, set())
            if fixed:
                available_ids = [did for did in fixed if did in doc_id_to_name]
            else:
                available_ids = [d["id"] for d in doctors]
            available_ids = sorted(available_ids, key=lambda x: doc_id_to_name.get(x, ""))
            options = [""] + available_ids

            default = slot_map.get((ds, cid), "")
            try:
                idx = options.index(default)
            except ValueError:
                # 既存割当が候補に含まれない場合は先頭に追加
                options = [default] + options
                idx = 0

            ds_local = ds
            def fmt(opt, _ds=ds_local):
                if not opt:
                    return "—"
                name = doc_id_to_name.get(opt, f"ID:{opt}")
                if _ds in ng_map.get(opt, set()):
                    return f"⛔ {name}（×NG）"
                if _ds in avoid_map.get(opt, set()):
                    return f"⚠ {name}（△）"
                return name

            key = f"slot_{target_month}_{sched_id}_{ds}_{cid}_v{edit_ver}"
            with row_cols[i + 1]:
                sel = st.selectbox(
                    f"{label} {clinic_id_to_name.get(cid, '?')}",
                    options=options,
                    index=idx,
                    format_func=fmt,
                    key=key,
                    label_visibility="collapsed",
                )
            edited_slots[(ds, cid)] = sel if sel else None

    return edited_slots


def _render_live_warnings(edited_slots, constraints, doc_id_to_name, clinic_id_to_name):
    """現在の編集状態に対するNG/△の警告をリアルタイム表示"""
    ng_map = constraints["ng_map"]
    avoid_map = constraints["avoid_map"]
    ng_hits = []
    avoid_hits = []
    for (ds, cid), did in sorted(edited_slots.items()):
        if not did:
            continue
        dname = doc_id_to_name.get(did, "?")
        cname = clinic_id_to_name.get(cid, "?")
        label = date.fromisoformat(ds).strftime("%m/%d")
        if ds in ng_map.get(did, set()):
            ng_hits.append(f"⛔ {label} {cname}：{dname}（×NG日）")
        elif ds in avoid_map.get(did, set()):
            avoid_hits.append(f"⚠ {label} {cname}：{dname}（△）")

    if ng_hits:
        st.error("**NG日に割り当てがあります（保存時に再確認されます）**\n\n" + "\n\n".join(f"- {h}" for h in ng_hits))
    if avoid_hits:
        st.warning("**『できれば避けたい』日に割り当てがあります**\n\n" + "\n\n".join(f"- {h}" for h in avoid_hits))


def _check_soft_constraints(new_assignments, constraints, doctors):
    """ソフト制約違反の警告メッセージリストを返す（NG/△/希望外/上限超過）"""
    doc_name_map = build_display_name_map(doctors)
    ng_map = constraints["ng_map"]
    avoid_map = constraints["avoid_map"]
    max_assignments_map = constraints["max_assignments"]
    date_clinic_req_map = constraints["date_clinic_requests"]

    warnings = []

    for a in new_assignments:
        did, ds, cid = a["doctor_id"], a["date"], a["clinic_id"]
        dname = doc_name_map.get(did, "?")
        d_obj = date.fromisoformat(ds)
        if ds in ng_map.get(did, set()):
            warnings.append(
                f"⛔ {dname} は {d_obj.strftime('%m/%d')} を「×（NG）」に設定しています"
            )
        elif ds in avoid_map.get(did, set()):
            warnings.append(
                f"⚠ {dname} は {d_obj.strftime('%m/%d')} を「△（できれば避けたい）」に設定しています"
            )
        requested_cid = date_clinic_req_map.get(did, {}).get(ds)
        if requested_cid is not None and int(requested_cid) != cid:
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


def _validate_and_convert(edited_slots, dates, clinics_in_sched,
                          doc_id_to_name, clinic_id_to_name, constraints):
    """編集状態 (edited_slots) を assignments に変換 + ハード制約チェック

    NG/△ は降格してソフト警告（_check_soft_constraints）で扱う。
    ここでは保存不可レベルの違反のみエラー化する。
    """
    new_assignments = []
    errors = []

    ds_pair_set = constraints.get("double_shift_pairs_set", set())

    for ds in dates:
        d_obj = date.fromisoformat(ds)
        day_label = d_obj.strftime("%m/%d(%a)")
        day_doctor_clinics = {}

        for cid in clinics_in_sched:
            cname = clinic_id_to_name.get(cid, "?")
            did = edited_slots.get((ds, cid))
            if not did:
                continue

            dname = doc_id_to_name.get(did, f"ID:{did}")

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
