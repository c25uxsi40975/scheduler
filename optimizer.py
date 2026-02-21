"""
スケジューリング最適化モジュール
PuLPを使用した制約付き最適化で外勤割り当てを生成
"""
import json
from datetime import date, timedelta
import jpholiday
import pulp
import numpy as np


# 優先度の weight 値定義
PRIORITY_MUST = 2.0      # ◎ 月1回以上必ず行く
PRIORITY_POSSIBLE = 1.0  # ○ 行くときもある
PRIORITY_NEVER = 0.0     # × まったく行かない


def get_target_saturdays(year: int, month: int) -> list[date]:
    """指定月の土曜日を取得（祝日除外）"""
    saturdays = []
    d = date(year, month, 1)
    while d.month == month:
        if d.weekday() == 5 and not jpholiday.is_holiday(d):
            saturdays.append(d)
        d += timedelta(days=1)
    return saturdays


def get_clinic_dates(clinic: dict, saturdays: list[date]) -> list[date]:
    """外勤先の頻度に応じた対象日を返す"""
    freq = clinic.get("frequency", "weekly")
    if freq == "weekly":
        return saturdays
    elif freq == "biweekly_odd":
        return [s for i, s in enumerate(saturdays) if i % 2 == 0]
    elif freq == "biweekly_even":
        return [s for i, s in enumerate(saturdays) if i % 2 == 1]
    elif freq == "first_only":
        return saturdays[:1] if saturdays else []
    elif freq == "last_only":
        return saturdays[-1:] if saturdays else []
    else:
        return saturdays


def solve_schedule(
    doctors: list[dict],
    clinics: list[dict],
    saturdays: list[date],
    preferences: list[dict],
    affinities: list[dict],
    mode: str = "balanced",
    previous_earnings: dict = None,
    date_overrides: dict = None,
) -> dict | None:
    """
    最適化ソルバー

    mode:
      - "balanced": 給与ばらつき最小化を重視
      - "preference": 希望重視
      - "affinity": 優先度重視
    """
    doc_ids = [d["id"] for d in doctors]
    clinic_list = clinics

    # 日別オーバーライド: {(clinic_id, date_str): required_doctors}
    overrides = date_overrides or {}

    # 各外勤先の対象日（休診=0 を除外）
    clinic_dates = {}
    slot_required = {}  # スロットごとの必要医員数
    for c in clinic_list:
        cd = get_clinic_dates(c, saturdays)
        for d in cd:
            ds = d.isoformat()
            req = overrides.get((c["id"], ds), 1)
            if req == 0:
                continue  # 休診: スロットを生成しない
            clinic_dates[(c["id"], ds)] = True
            slot_required[(c["id"], ds)] = req

    # 全スロット: (clinic_id, date_str)
    slots = list(clinic_dates.keys())
    if not slots:
        return None

    # NG日・△日マップ
    ng_map = {}
    avoid_map = {}
    pref_clinics_map = {}
    for p in preferences:
        ng_map[p["doctor_id"]] = set(p.get("ng_dates", []))
        avoid_map[p["doctor_id"]] = set(p.get("avoid_dates", []))
        pref_clinics_map[p["doctor_id"]] = set(p.get("preferred_clinics", []))

    # 日別外勤先希望マップ: {doctor_id: {date_str: clinic_id}}
    date_clinic_req_map = {}
    for p in preferences:
        dcr = p.get("date_clinic_requests", {})
        if dcr:
            date_clinic_req_map[p["doctor_id"]] = {
                ds: int(cid) for ds, cid in dcr.items()
            }

    # 外勤先の希望医員マップ
    clinic_preferred = {}
    for c in clinic_list:
        pref = c.get("preferred_doctors", "[]")
        if isinstance(pref, str):
            pref = json.loads(pref)
        clinic_preferred[c["id"]] = set(pref)

    # 外勤先の固定メンバーマップ
    clinic_fixed = {}
    for c in clinic_list:
        fixed = c.get("fixed_doctors", [])
        if isinstance(fixed, str):
            fixed = json.loads(fixed)
        clinic_fixed[c["id"]] = set(fixed)

    # 優先度マップ (weight: ◎=2.0, ○=1.0, ×=0.0)
    priority_map = {}
    for a in affinities:
        priority_map[(a["doctor_id"], a["clinic_id"])] = a["weight"]

    # ◎(must)リスト: 医員→外勤先ID のリスト
    must_pairs = {}
    never_pairs = {}
    for (did, cid), w in priority_map.items():
        if w == PRIORITY_MUST:
            must_pairs.setdefault(did, []).append(cid)
        elif w == PRIORITY_NEVER:
            never_pairs.setdefault(did, []).append(cid)

    # 報酬マップ
    fee_map = {c["id"]: c.get("fee", 0) for c in clinic_list}

    # ---- PuLP モデル ----
    prob = pulp.LpProblem("GaikinSchedule", pulp.LpMinimize)

    # 決定変数: x[doc_id, clinic_id, date_str] ∈ {0,1}
    x = {}
    for doc_id in doc_ids:
        for (cid, ds) in slots:
            x[(doc_id, cid, ds)] = pulp.LpVariable(
                f"x_{doc_id}_{cid}_{ds}", cat=pulp.LpBinary
            )

    # ---- 制約条件 ----

    # 1. 各スロットに必要人数を割り当て（通常1人、2人体制の場合2人）
    for (cid, ds) in slots:
        req = slot_required.get((cid, ds), 1)
        prob += (
            pulp.lpSum(x[(doc_id, cid, ds)] for doc_id in doc_ids) == req,
            f"slot_req_{cid}_{ds}"
        )

    # 2. 各医員は同一日に最大1外勤
    all_dates = sorted(set(ds for _, ds in slots))
    for doc_id in doc_ids:
        for ds in all_dates:
            relevant = [(cid, ds2) for (cid, ds2) in slots if ds2 == ds]
            if relevant:
                prob += (
                    pulp.lpSum(x[(doc_id, cid, ds2)] for (cid, ds2) in relevant) <= 1,
                    f"one_per_day_{doc_id}_{ds}"
                )

    # 3. ×日（NG）は割り当て不可
    for doc_id in doc_ids:
        ng_dates = ng_map.get(doc_id, set())
        for (cid, ds) in slots:
            if ds in ng_dates:
                prob += x[(doc_id, cid, ds)] == 0, f"ng_{doc_id}_{cid}_{ds}"

    # 4. ×外勤先（never）は割り当て不可
    for doc_id in doc_ids:
        for cid in never_pairs.get(doc_id, []):
            for (slot_cid, ds) in slots:
                if slot_cid == cid:
                    prob += x[(doc_id, cid, ds)] == 0, f"never_{doc_id}_{cid}_{ds}"

    # 5. ◎外勤先（must）は月1回以上割り当て
    for doc_id in doc_ids:
        for cid in must_pairs.get(doc_id, []):
            clinic_slots = [(c, d) for (c, d) in slots if c == cid]
            if clinic_slots:
                prob += (
                    pulp.lpSum(x[(doc_id, c, d)] for (c, d) in clinic_slots) >= 1,
                    f"must_{doc_id}_{cid}"
                )

    # 6. 固定メンバーは必ず割り当て（NG日を除く）
    for (cid, ds) in slots:
        for doc_id in clinic_fixed.get(cid, set()):
            if doc_id in doc_ids and ds not in ng_map.get(doc_id, set()):
                prob += x[(doc_id, cid, ds)] == 1, f"fixed_{doc_id}_{cid}_{ds}"

    # 7. 月回数上限（max_assignments > 0 の場合）
    for doc in doctors:
        max_a = doc.get("max_assignments", 0)
        if max_a > 0:
            prob += (
                pulp.lpSum(x[(doc["id"], cid, ds)] for (cid, ds) in slots) <= max_a,
                f"max_assign_{doc['id']}"
            )

    # ---- 目的関数 ----

    # 各医員の報酬合計
    earnings = {}
    for doc_id in doc_ids:
        earnings[doc_id] = pulp.lpSum(
            x[(doc_id, cid, ds)] * fee_map.get(cid, 0)
            for (cid, ds) in slots
        )

    # 前月までの累計を考慮
    prev = previous_earnings or {}
    total_earnings = {}
    for doc_id in doc_ids:
        total_earnings[doc_id] = earnings[doc_id] + prev.get(doc_id, 0)

    # 平均報酬
    n_docs = len(doc_ids)
    avg_earning = pulp.lpSum(total_earnings[d] for d in doc_ids) / n_docs

    # ばらつき（線形近似: 各医員と平均の差の絶対値の和）
    dev_plus = {}
    dev_minus = {}
    for doc_id in doc_ids:
        dev_plus[doc_id] = pulp.LpVariable(f"dev_p_{doc_id}", lowBound=0)
        dev_minus[doc_id] = pulp.LpVariable(f"dev_m_{doc_id}", lowBound=0)
        prob += total_earnings[doc_id] - avg_earning == dev_plus[doc_id] - dev_minus[doc_id]

    variance_term = pulp.lpSum(dev_plus[d] + dev_minus[d] for d in doc_ids)

    # 希望スコア（医員が希望した外勤先に行けるとプラス）
    preference_term = pulp.lpSum(
        x[(doc_id, cid, ds)]
        for doc_id in doc_ids
        for (cid, ds) in slots
        if cid in pref_clinics_map.get(doc_id, set())
    )

    # 指名スコア（外勤先が指名した医員に来てもらえるとプラス）
    nomination_term = pulp.lpSum(
        x[(doc_id, cid, ds)]
        for doc_id in doc_ids
        for (cid, ds) in slots
        if doc_id in clinic_preferred.get(cid, set())
    )

    # 優先度スコア（◎=2, ○=1 の外勤先に行くとプラス）
    priority_term = pulp.lpSum(
        x[(doc_id, cid, ds)] * priority_map.get((doc_id, cid), 0)
        for doc_id in doc_ids
        for (cid, ds) in slots
    )

    # △日ペナルティ（できれば避けたい日に割り当てるとペナルティ）
    avoid_penalty = pulp.lpSum(
        x[(doc_id, cid, ds)]
        for doc_id in doc_ids
        for (cid, ds) in slots
        if ds in avoid_map.get(doc_id, set())
    )

    # 回数の均等性（各医員の総外勤回数のばらつき）
    count_per_doc = {}
    for doc_id in doc_ids:
        count_per_doc[doc_id] = pulp.lpSum(
            x[(doc_id, cid, ds)] for (cid, ds) in slots
        )
    avg_count = pulp.lpSum(count_per_doc[d] for d in doc_ids) / n_docs
    count_dev_p = {}
    count_dev_m = {}
    for doc_id in doc_ids:
        count_dev_p[doc_id] = pulp.LpVariable(f"cnt_dp_{doc_id}", lowBound=0)
        count_dev_m[doc_id] = pulp.LpVariable(f"cnt_dm_{doc_id}", lowBound=0)
        prob += count_per_doc[doc_id] - avg_count == count_dev_p[doc_id] - count_dev_m[doc_id]
    count_variance = pulp.lpSum(count_dev_p[d] + count_dev_m[d] for d in doc_ids)

    # 日別外勤先希望ボーナス（特定日に特定外勤先を希望した場合のプラス）
    date_clinic_bonus = pulp.lpSum(
        x[(doc_id, cid, ds)]
        for doc_id in doc_ids
        for (cid, ds) in slots
        if date_clinic_req_map.get(doc_id, {}).get(ds) == cid
    )

    # モードに応じた重み設定
    #   w_var:  報酬ばらつき
    #   w_pref: 医員希望外勤先
    #   w_nom:  外勤先指名医員
    #   w_pri:  優先度(◎○×)
    #   w_avoid: △日ペナルティ
    #   w_cnt:  回数均等
    #   w_dcr:  日別外勤先希望
    if mode == "balanced":
        w_var, w_pref, w_nom, w_pri, w_avoid, w_cnt, w_dcr = 10.0, -1.0, -2.0, -1.0, 3.0, 5.0, -3.0
    elif mode == "preference":
        w_var, w_pref, w_nom, w_pri, w_avoid, w_cnt, w_dcr = 2.0, -5.0, -3.0, -2.0, 3.0, 3.0, -5.0
    elif mode == "affinity":
        w_var, w_pref, w_nom, w_pri, w_avoid, w_cnt, w_dcr = 2.0, -2.0, -2.0, -5.0, 3.0, 3.0, -3.0
    else:
        w_var, w_pref, w_nom, w_pri, w_avoid, w_cnt, w_dcr = 5.0, -2.0, -2.0, -2.0, 3.0, 5.0, -3.0

    # 報酬が0の場合は回数均等をメインにする
    if all(fee_map.get(cid, 0) == 0 for cid in fee_map):
        w_var = 0

    prob += (
        w_var * variance_term
        + w_pref * preference_term
        + w_nom * nomination_term
        + w_pri * priority_term
        + w_avoid * avoid_penalty
        + w_cnt * count_variance
        + w_dcr * date_clinic_bonus
    )

    # ---- 求解 ----
    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=30)
    status = prob.solve(solver)

    if pulp.LpStatus[status] != "Optimal":
        return None

    # ---- 結果抽出 ----
    assignments = []
    for (cid, ds) in slots:
        for doc_id in doc_ids:
            if pulp.value(x[(doc_id, cid, ds)]) > 0.5:
                assignments.append({
                    "date": ds,
                    "clinic_id": cid,
                    "doctor_id": doc_id,
                })

    # 統計
    doc_earnings = {d: 0 for d in doc_ids}
    doc_counts = {d: 0 for d in doc_ids}
    for a in assignments:
        doc_earnings[a["doctor_id"]] += fee_map.get(a["clinic_id"], 0)
        doc_counts[a["doctor_id"]] += 1

    earnings_list = list(doc_earnings.values())
    total_var = float(np.std(earnings_list)) if earnings_list else 0

    # 満足度スコア
    sat = 0
    for a in assignments:
        did, cid = a["doctor_id"], a["clinic_id"]
        if cid in pref_clinics_map.get(did, set()):
            sat += 1
        if did in clinic_preferred.get(cid, set()):
            sat += 1
        sat += priority_map.get((did, cid), 0)

    return {
        "assignments": assignments,
        "doctor_earnings": doc_earnings,
        "doctor_counts": doc_counts,
        "total_variance": total_var,
        "satisfaction_score": float(sat),
        "status": pulp.LpStatus[status],
    }


def generate_multiple_plans(
    doctors, clinics, saturdays, preferences, affinities,
    previous_earnings=None, date_overrides=None,
) -> list[dict]:
    """複数のプラン（案）を生成"""
    plans = []
    modes = [
        ("balanced", "案A: 給与均等重視"),
        ("preference", "案B: 希望重視"),
        ("affinity", "案C: 優先度重視"),
    ]
    for mode, label in modes:
        result = solve_schedule(
            doctors, clinics, saturdays, preferences, affinities,
            mode=mode, previous_earnings=previous_earnings,
            date_overrides=date_overrides,
        )
        if result:
            result["plan_name"] = label
            result["mode"] = mode
            plans.append(result)
    return plans
