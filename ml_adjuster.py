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
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from datetime import date
from dateutil.relativedelta import relativedelta
from pathlib import Path

from optimizer import get_target_saturdays, get_clinic_dates


_model = None
_suitability_model = None
_MODEL_PATH = Path(__file__).parent / "model.pkl"
_SUITABILITY_MODEL_PATH = Path(__file__).parent / "suitability_model.pkl"
_MIN_TRAINING_ROWS = 50

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


def _train_model(df):
    """DataFrameからパイプライン（Imputer+RandomForest）を学習"""
    X = df[FEATURE_COLUMNS]
    y = df["労力コスト"]
    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("rf", RandomForestRegressor(n_estimators=100, random_state=42)),
    ])
    pipeline.fit(X, y)
    return pipeline


def _load_or_train_model():
    """学習テーブルのデータでオンザフライ学習。不足時はmodel.pklフォールバック"""
    global _model
    if _model is not None:
        return _model

    from database import get_training_data
    df = get_training_data()

    if len(df) >= _MIN_TRAINING_ROWS:
        _model = _train_model(df)
    else:
        if _MODEL_PATH.exists():
            _model = joblib.load(_MODEL_PATH)
        else:
            raise RuntimeError(
                f"学習データが不足({len(df)}行 < {_MIN_TRAINING_ROWS}行)で、"
                "model.pklも見つかりません"
            )
    return _model


def _clear_model():
    """キャッシュ済みモデルをクリア（再学習後に呼び出す）"""
    global _model
    _model = None


def get_model_metrics():
    """現在のモデルの学習データ情報を返す"""
    from database import get_training_data
    df = get_training_data()
    return {
        "training_rows": len(df),
        "min_required": _MIN_TRAINING_ROWS,
        "using_local_model": len(df) < _MIN_TRAINING_ROWS,
    }


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
    """[Deprecated] 全医員のML予測値を一括計算。{doctor_id: predicted_effort_cost} を返す。

    ペア適合性モデル (compute_suitability_matrix) に置き換え済み。
    """
    model = _load_or_train_model()

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
                       pre_assigned, slot_required_map,
                       fixed_members=None, excluded_members=None):
    """1日分の割当をlinear_sum_assignmentで最適化。"""
    date_str = saturday.isoformat()
    assignments = list(pre_assigned)
    fixed_members = fixed_members or {}
    excluded_members = excluded_members or {}

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
            # 固定メンバー制約: リスト外の医員は割り当て不可
            fixed = fixed_members.get(cid, set())
            if fixed and doc["id"] not in fixed:
                continue
            # 除外メンバー制約: 除外メンバーは割り当て不可
            if doc["id"] in excluded_members.get(cid, set()):
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
    """[Deprecated] 月全体のML再調整。制約を尊重しつつ、モデル予測ベースで最適割当を生成。

    統合パイプライン (pipeline.run_integrated_pipeline) に置き換え済み。
    """
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

    excluded_members = {}
    for c in clinics:
        excluded = c.get("excluded_doctors", [])
        if isinstance(excluded, str):
            excluded = json.loads(excluded)
        if excluded:
            excluded_members[c["id"]] = set(excluded)

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

        # ML割当に利用可能な医員
        available = [
            d for d in doctors
            if date_str not in ng_map.get(d["id"], set())
            and (max_assign[d["id"]] == 0 or doc_assign_count[d["id"]] < max_assign[d["id"]])
        ]

        # この日の割当を最適化
        date_assignments = _solve_single_date(
            saturday, available, active_clinics,
            predictions, ng_map, never_pairs,
            [], slot_req_map,
            fixed_members, excluded_members,
        )

        # カウント更新
        for a in date_assignments:
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


# ==================================================================
# Suitability Model (ペア適合性モデル)
# ==================================================================

PAIR_FEATURE_COLUMNS = [
    # Doctor-side (6)
    "採用年度", "役職ランク",
    "過去3ヶ月平均労力コスト", "過去3ヶ月平均給与",
    "過去3ヶ月割当回数", "当月累積給与",
    # Clinic-side (4)
    "外勤先_労力コスト", "外勤先_給与", "外勤先_勤務時間", "外勤先_時間帯",
    # Interaction (4)
    "労力差", "過去ペア回数", "優先度重み", "給与ランク積",
]

_TIME_SLOT_MAP = {"AM": 0, "PM": 1, "ALL": 2, "": 1}


def _compute_doctor_history(doctor, clinics, confirmed_schedules, target_month):
    """医員の履歴情報を計算（ペア特徴量のDoctor-side用）。

    Returns:
        dict with keys:
            hiring_year, job_rank, avg_effort_3m, avg_fee_3m,
            assign_count_3m, cumulative_fee,
            pair_counts: {clinic_id: count},
            assignments: sorted list of assignment dicts
    """
    clinic_effort = {c["id"]: c.get("effort_cost", 0) for c in clinics}
    clinic_fee = {c["id"]: c.get("fee", 0) for c in clinics}

    ty, tm = map(int, target_month.split("-"))
    target_start = date(ty, tm, 1)
    window_start = target_start - relativedelta(months=3)

    assignments = []
    pair_counts = {}
    cumulative_fee = 0.0
    for sched in confirmed_schedules:
        if sched["year_month"] >= target_month:
            continue
        for a in sched.get("assignments", []):
            if a["doctor_id"] == doctor["id"]:
                cid = a["clinic_id"]
                fee = float(clinic_fee.get(cid, 0))
                a_date = date.fromisoformat(a["date"])
                assignments.append({
                    "date": a_date,
                    "effort_cost": float(clinic_effort.get(cid, 0)),
                    "fee": fee,
                    "clinic_id": cid,
                })
                pair_counts[cid] = pair_counts.get(cid, 0) + 1
                cumulative_fee += fee
    assignments.sort(key=lambda x: x["date"])

    window_assignments = [
        a for a in assignments
        if window_start <= a["date"] < target_start
    ]

    try:
        hiring_year = int(doctor["account"])
    except (ValueError, TypeError):
        hiring_year = 2020

    job_rank = doctor.get("job_rank", 0)
    job_rank = float(job_rank) if job_rank else np.nan

    avg_effort_3m = (
        np.mean([a["effort_cost"] for a in window_assignments])
        if window_assignments else np.nan
    )
    avg_fee_3m = (
        np.mean([a["fee"] for a in window_assignments])
        if window_assignments else np.nan
    )
    assign_count_3m = len(window_assignments)

    return {
        "hiring_year": hiring_year,
        "job_rank": job_rank,
        "avg_effort_3m": avg_effort_3m,
        "avg_fee_3m": avg_fee_3m,
        "assign_count_3m": assign_count_3m,
        "cumulative_fee": cumulative_fee,
        "pair_counts": pair_counts,
    }


def compute_pair_features(doctor_history, clinic, affinities_map):
    """1つの (doctor, clinic) ペアの14特徴量を計算。

    Args:
        doctor_history: _compute_doctor_history() の返り値
        clinic: clinic dict (id, fee, effort_cost, work_hours, time_slot, ...)
        affinities_map: {(doctor_id, clinic_id): weight} 辞書

    Returns:
        dict of 14 feature values keyed by PAIR_FEATURE_COLUMNS
    """
    dh = doctor_history
    cid = clinic["id"]

    effort_cost = float(clinic.get("effort_cost", 0) or 0)
    fee = float(clinic.get("fee", 0) or 0)
    work_hours = float(clinic.get("work_hours", 0) or 0)
    time_slot = _TIME_SLOT_MAP.get(clinic.get("time_slot", ""), 1)

    avg_effort = dh["avg_effort_3m"]
    effort_gap = abs(avg_effort - effort_cost) if not np.isnan(avg_effort) else np.nan

    pair_count = dh["pair_counts"].get(cid, 0)
    weight = affinities_map.get(cid, 1.0)

    job_rank = dh["job_rank"]
    fee_rank = fee * job_rank if not np.isnan(job_rank) else np.nan

    return {
        "採用年度": dh["hiring_year"],
        "役職ランク": dh["job_rank"],
        "過去3ヶ月平均労力コスト": dh["avg_effort_3m"],
        "過去3ヶ月平均給与": dh["avg_fee_3m"],
        "過去3ヶ月割当回数": dh["assign_count_3m"],
        "当月累積給与": dh["cumulative_fee"],
        "外勤先_労力コスト": effort_cost,
        "外勤先_給与": fee,
        "外勤先_勤務時間": work_hours,
        "外勤先_時間帯": time_slot,
        "労力差": effort_gap,
        "過去ペア回数": pair_count,
        "優先度重み": weight,
        "給与ランク積": fee_rank,
    }


def _train_suitability_model(df):
    """適合学習データから二値分類モデルを学習"""
    X = df[PAIR_FEATURE_COLUMNS]
    y = df["割当結果"]
    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("rf", RandomForestClassifier(
            n_estimators=200,
            max_depth=10,
            class_weight="balanced",
            random_state=42,
        )),
    ])
    pipeline.fit(X, y)
    return pipeline


def _load_or_train_suitability_model():
    """適合学習テーブルからモデルを学習。不足時はフォールバック用にNoneを返す。"""
    global _suitability_model
    if _suitability_model is not None:
        return _suitability_model

    from database import get_suitability_training_data
    df = get_suitability_training_data()

    if len(df) >= _MIN_TRAINING_ROWS:
        _suitability_model = _train_suitability_model(df)
        return _suitability_model

    if _SUITABILITY_MODEL_PATH.exists():
        _suitability_model = joblib.load(_SUITABILITY_MODEL_PATH)
        return _suitability_model

    return None  # フォールバックモードへ


def _clear_suitability_model():
    """適合性モデルのキャッシュをクリア"""
    global _suitability_model
    _suitability_model = None


def get_suitability_model_metrics():
    """適合性モデルの学習データ情報を返す"""
    from database import get_suitability_training_data
    df = get_suitability_training_data()
    positive = int((df["割当結果"] == 1).sum()) if len(df) > 0 else 0
    negative = int((df["割当結果"] == 0).sum()) if len(df) > 0 else 0
    return {
        "training_rows": len(df),
        "positive_samples": positive,
        "negative_samples": negative,
        "min_required": _MIN_TRAINING_ROWS,
        "model_available": len(df) >= _MIN_TRAINING_ROWS or _SUITABILITY_MODEL_PATH.exists(),
        "using_fallback": len(df) < _MIN_TRAINING_ROWS,
    }


def _fallback_suitability_score(doctor_history, clinic):
    """適合性モデルが利用不可の場合のヒューリスティックスコア。
    旧effort_costモデルの予測値との差をベースに0-1スコアを生成。"""
    avg_effort = doctor_history["avg_effort_3m"]
    effort_cost = float(clinic.get("effort_cost", 5) or 5)

    if np.isnan(avg_effort):
        return 0.5  # 履歴なし → 中立スコア

    # effort差が小さいほどスコアが高い (0-1に正規化)
    effort_gap = abs(avg_effort - effort_cost)
    return max(0.0, 1.0 - effort_gap / 10.0)


def compute_suitability_matrix(doctors, clinics, confirmed_schedules,
                                affinities, target_month):
    """全 (doctor, clinic) ペアの適合性スコア行列を計算。

    Returns:
        dict: {(doctor_id, clinic_id): score} where score is 0.0-1.0
    """
    # 優先度マスタを (doctor_id, clinic_id) -> weight の辞書に変換
    affinities_by_doctor = {}
    for a in affinities:
        affinities_by_doctor.setdefault(a["doctor_id"], {})[a["clinic_id"]] = a["weight"]

    # 全医員の履歴を事前計算
    doctor_histories = {}
    for doc in doctors:
        doctor_histories[doc["id"]] = _compute_doctor_history(
            doc, clinics, confirmed_schedules, target_month
        )

    model = _load_or_train_suitability_model()

    if model is not None:
        # MLモデルによるスコア計算
        rows = []
        pairs = []
        for doc in doctors:
            dh = doctor_histories[doc["id"]]
            aff_map = affinities_by_doctor.get(doc["id"], {})
            for clinic in clinics:
                features = compute_pair_features(dh, clinic, aff_map)
                rows.append([features[col] for col in PAIR_FEATURE_COLUMNS])
                pairs.append((doc["id"], clinic["id"]))

        df = pd.DataFrame(rows, columns=PAIR_FEATURE_COLUMNS)
        scores = model.predict_proba(df)[:, 1]
        return {pair: float(score) for pair, score in zip(pairs, scores)}
    else:
        # フォールバック: ヒューリスティックスコア
        result = {}
        for doc in doctors:
            dh = doctor_histories[doc["id"]]
            aff_map = affinities_by_doctor.get(doc["id"], {})
            for clinic in clinics:
                base_score = _fallback_suitability_score(dh, clinic)
                # 優先度重みで調整 (×=0.0, ○=1.0, ◎=2.0)
                weight = aff_map.get(clinic["id"], 1.0)
                adjusted = base_score * (weight / 2.0) if weight > 0 else 0.0
                result[(doc["id"], clinic["id"])] = min(1.0, adjusted)
        return result
