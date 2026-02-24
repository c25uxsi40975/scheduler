"""
ML-based schedule readjustment module.
RandomForestモデルで医員ごとの妥当な労力コストを予測し、
scipy.optimize.linear_sum_assignmentで最適な割り当てを求める。
"""
import json
import joblib
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from datetime import date
from dateutil.relativedelta import relativedelta
from pathlib import Path

from optimizer import get_target_saturdays, get_clinic_dates


_model = None
_MODEL_PATH = Path(__file__).parent / "model.pkl"

FEATURE_COLUMNS = [
    "採用年度",
    "役職ランク",
    "過去3ヶ月平均労力コスト",
    "前週労力コスト",
    "労力コスト最大累計回数",
    "直近労力コスト最大からの経過週",
    "前週給与",
    "過去3ヶ月平均給与",
    "過去3ヶ月累積給与",
]


def _load_model():
    global _model
    if _model is None:
        _model = joblib.load(_MODEL_PATH)
    return _model


def compute_doctor_features(doctor, clinics, confirmed_schedules, target_month):
    """1医員の9特徴量を計算。対象月より前の確定スケジュールから履歴を抽出。"""
    clinic_effort = {c["id"]: c.get("effort_cost", 0) for c in clinics}
    clinic_fee = {c["id"]: c.get("fee", 0) for c in clinics}

    ty, tm = map(int, target_month.split("-"))
    target_start = date(ty, tm, 1)
    window_start = target_start - relativedelta(months=3)

    # この医員の過去アサインメントを収集
    assignments = []
    for sched in confirmed_schedules:
        if sched["year_month"] >= target_month:
            continue
        for a in sched.get("assignments", []):
            if a["doctor_id"] == doctor["id"]:
                a_date = date.fromisoformat(a["date"])
                assignments.append({
                    "date": a_date,
                    "effort_cost": float(clinic_effort.get(a["clinic_id"], 0)),
                    "fee": float(clinic_fee.get(a["clinic_id"], 0)),
                })
    assignments.sort(key=lambda x: x["date"])

    # Feature 1: 採用年度
    try:
        hiring_year = int(doctor["account"])
    except (ValueError, TypeError):
        hiring_year = 2020

    # Feature 2: 役職ランク
    job_rank = doctor.get("job_rank", 0)
    job_rank = float(job_rank) if job_rank else np.nan

    # 3ヶ月窓内のアサインメント
    window_assignments = [
        a for a in assignments
        if window_start <= a["date"] < target_start
    ]

    # Feature 3: 過去3ヶ月平均労力コスト
    avg_effort_3m = (
        np.mean([a["effort_cost"] for a in window_assignments])
        if window_assignments else np.nan
    )

    # Feature 4: 前週労力コスト（対象月開始前の直近）
    recent = [a for a in assignments if a["date"] < target_start]
    last_effort = recent[-1]["effort_cost"] if recent else np.nan

    # Feature 5: 労力コスト最大累計回数
    max_cost_count = sum(1 for a in assignments if a["effort_cost"] >= 10.0)

    # Feature 6: 直近労力コスト最大からの経過週
    max_cost_dates = [a for a in assignments if a["effort_cost"] >= 10.0]
    if max_cost_dates:
        last_max_date = max_cost_dates[-1]["date"]
        weeks_since_max = float((target_start - last_max_date).days // 7)
    else:
        weeks_since_max = np.nan

    # Feature 7: 前週給与
    last_fee = recent[-1]["fee"] if recent else np.nan

    # Feature 8: 過去3ヶ月平均給与
    avg_fee_3m = (
        np.mean([a["fee"] for a in window_assignments])
        if window_assignments else np.nan
    )

    # Feature 9: 過去3ヶ月累積給与
    total_fee_3m = (
        float(sum(a["fee"] for a in window_assignments))
        if window_assignments else np.nan
    )

    return {
        "採用年度": hiring_year,
        "役職ランク": job_rank,
        "過去3ヶ月平均労力コスト": avg_effort_3m,
        "前週労力コスト": last_effort,
        "労力コスト最大累計回数": max_cost_count,
        "直近労力コスト最大からの経過週": weeks_since_max,
        "前週給与": last_fee,
        "過去3ヶ月平均給与": avg_fee_3m,
        "過去3ヶ月累積給与": total_fee_3m,
    }


def predict_effort_costs(doctors, clinics, confirmed_schedules, target_month):
    """全医員のML予測値を一括計算。{doctor_id: predicted_effort_cost} を返す。"""
    model = _load_model()

    rows = []
    doc_ids = []
    for doc in doctors:
        features = compute_doctor_features(doc, clinics, confirmed_schedules, target_month)
        rows.append(features)
        doc_ids.append(doc["id"])

    df = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    predictions = model.predict(df)
    return {did: float(pred) for did, pred in zip(doc_ids, predictions)}


def _solve_single_date(saturday, available_doctors, active_clinics,
                       predictions, ng_map, never_pairs,
                       pre_assigned, slot_required_map):
    """1日分の割当をlinear_sum_assignmentで最適化。"""
    date_str = saturday.isoformat()
    assignments = list(pre_assigned)

    used_doctors = {a["doctor_id"] for a in pre_assigned}
    clinic_remaining = {}
    for c in active_clinics:
        req = slot_required_map.get(c["id"], 1)
        already = sum(1 for a in pre_assigned if a["clinic_id"] == c["id"])
        remaining = req - already
        if remaining > 0:
            clinic_remaining[c["id"]] = remaining

    free_doctors = [
        d for d in available_doctors
        if d["id"] not in used_doctors
        and date_str not in ng_map.get(d["id"], set())
    ]

    if not free_doctors or not clinic_remaining:
        return assignments

    # スロットを展開（2人体制の場合は同一外勤先が複数列になる）
    clinic_slots = []
    clinic_effort = {}
    for c in active_clinics:
        clinic_effort[c["id"]] = c.get("effort_cost", 5.0)
        for _ in range(clinic_remaining.get(c["id"], 0)):
            clinic_slots.append(c["id"])

    if not clinic_slots:
        return assignments

    n_docs = len(free_doctors)
    n_slots = len(clinic_slots)
    INF = 1e9
    size = max(n_docs, n_slots)
    cost_matrix = np.full((size, size), INF)

    for i, doc in enumerate(free_doctors):
        doc_pred = predictions.get(doc["id"], 5.0)
        doc_never = set(never_pairs.get(doc["id"], []))
        for j, cid in enumerate(clinic_slots):
            if cid in doc_never:
                continue
            cost_matrix[i][j] = abs(doc_pred - clinic_effort.get(cid, 5.0))

    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    for r, c_idx in zip(row_ind, col_ind):
        if r < n_docs and c_idx < n_slots and cost_matrix[r][c_idx] < INF:
            assignments.append({
                "date": date_str,
                "clinic_id": clinic_slots[c_idx],
                "doctor_id": free_doctors[r]["id"],
            })

    return assignments


def ml_readjust(target_month, year, month, doctors, clinics,
                confirmed_schedules, preferences, affinities,
                date_overrides):
    """月全体のML再調整。制約を尊重しつつ、モデル予測ベースで最適割当を生成。"""
    saturdays = get_target_saturdays(year, month)
    if not saturdays:
        return None

    # 制約マップの構築
    ng_map = {}
    for p in preferences:
        ng_map[p["doctor_id"]] = set(p.get("ng_dates", []))

    never_pairs = {}
    must_pairs = {}
    for a in affinities:
        if a["weight"] == 0.0:
            never_pairs.setdefault(a["doctor_id"], []).append(a["clinic_id"])
        elif a["weight"] == 2.0:
            must_pairs.setdefault(a["doctor_id"], []).append(a["clinic_id"])

    fixed_members = {}
    for c in clinics:
        fixed = c.get("fixed_doctors", [])
        if isinstance(fixed, str):
            fixed = json.loads(fixed)
        if fixed:
            fixed_members[c["id"]] = set(fixed)

    # ML予測の実行
    predictions = predict_effort_costs(doctors, clinics, confirmed_schedules, target_month)

    # 医員ごとの割当カウント追跡
    doc_assign_count = {d["id"]: 0 for d in doctors}
    max_assign = {d["id"]: d.get("max_assignments", 0) for d in doctors}

    all_assignments = []
    fee_map = {c["id"]: c.get("fee", 0) for c in clinics}
    effort_map = {c["id"]: c.get("effort_cost", 0) for c in clinics}

    for saturday in saturdays:
        date_str = saturday.isoformat()

        # この日のアクティブな外勤先を決定
        active_clinics = []
        slot_req_map = {}
        for c in clinics:
            c_dates = get_clinic_dates(c, saturdays)
            if saturday not in c_dates:
                continue
            req = date_overrides.get((c["id"], date_str), 1)
            if req == 0:
                continue
            active_clinics.append(c)
            slot_req_map[c["id"]] = req

        # 固定メンバーを事前割当
        pre_assigned = []
        for c in active_clinics:
            for doc_id in fixed_members.get(c["id"], set()):
                if doc_id not in [d["id"] for d in doctors]:
                    continue
                if date_str in ng_map.get(doc_id, set()):
                    continue
                if max_assign[doc_id] > 0 and doc_assign_count[doc_id] >= max_assign[doc_id]:
                    continue
                if any(a["doctor_id"] == doc_id for a in pre_assigned):
                    continue
                pre_assigned.append({
                    "date": date_str,
                    "clinic_id": c["id"],
                    "doctor_id": doc_id,
                })
                doc_assign_count[doc_id] += 1

        # ML割当に利用可能な医員
        assigned_today = {a["doctor_id"] for a in pre_assigned}
        available = [
            d for d in doctors
            if d["id"] not in assigned_today
            and date_str not in ng_map.get(d["id"], set())
            and (max_assign[d["id"]] == 0 or doc_assign_count[d["id"]] < max_assign[d["id"]])
        ]

        # この日の割当を最適化
        date_assignments = _solve_single_date(
            saturday, available, active_clinics,
            predictions, ng_map, never_pairs,
            pre_assigned, slot_req_map,
        )

        # カウント更新（事前割当済みは除外）
        for a in date_assignments:
            if a not in pre_assigned:
                doc_assign_count[a["doctor_id"]] += 1

        all_assignments.extend(date_assignments)

    # 事後チェック: ◎制約の充足確認
    warnings = []
    doc_clinic_assigned = {}
    for a in all_assignments:
        doc_clinic_assigned.setdefault(a["doctor_id"], set()).add(a["clinic_id"])

    for doc_id, must_cids in must_pairs.items():
        for cid in must_cids:
            if cid not in doc_clinic_assigned.get(doc_id, set()):
                doc_name = next((d["name"] for d in doctors if d["id"] == doc_id), str(doc_id))
                cli_name = next((c["name"] for c in clinics if c["id"] == cid), str(cid))
                warnings.append(f"◎制約未充足: {doc_name} → {cli_name}")

    # 統計計算
    doc_earnings = {d["id"]: 0 for d in doctors}
    doc_counts = {d["id"]: 0 for d in doctors}
    effort_diffs = []

    for a in all_assignments:
        doc_earnings[a["doctor_id"]] += fee_map.get(a["clinic_id"], 0)
        doc_counts[a["doctor_id"]] += 1
        pred = predictions.get(a["doctor_id"], 5.0)
        actual = effort_map.get(a["clinic_id"], 5.0)
        effort_diffs.append(abs(pred - actual))

    earnings_list = list(doc_earnings.values())
    total_var = float(np.std(earnings_list)) if earnings_list else 0
    effort_match = float(np.mean(effort_diffs)) if effort_diffs else 0

    return {
        "assignments": all_assignments,
        "doctor_earnings": doc_earnings,
        "doctor_counts": doc_counts,
        "total_variance": total_var,
        "effort_match_score": effort_match,
        "warnings": warnings,
        "predictions": predictions,
    }
