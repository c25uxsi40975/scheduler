"""
スケジューリング共通ユーティリティ
土曜・平日セクションの両方で使用する対象日生成・制約チェック・基本最適化
"""
from datetime import date, timedelta
import jpholiday
import pulp


# ---- 対象日生成 ----

def get_target_dates(
    year: int,
    month: int,
    days_of_week: list[int],
    excluded: list[str] = None,
    extra: list[str] = None,
    exclude_holidays: bool = False,
) -> list[date]:
    """指定月の指定曜日の日付リストを生成

    Args:
        year, month: 対象年月
        days_of_week: 曜日リスト (0=月, 1=火, ..., 5=土, 6=日)
        excluded: 除外する日付文字列リスト ("YYYY-MM-DD")
        extra: 追加する日付文字列リスト ("YYYY-MM-DD", 翌月等)
        exclude_holidays: True の場合、祝日を自動除外
    """
    excluded_set = {date.fromisoformat(d) for d in (excluded or [])}
    days_set = set(days_of_week)

    dates = []
    d = date(year, month, 1)
    while d.month == month:
        if d.weekday() in days_set:
            if d not in excluded_set:
                if not exclude_holidays or not jpholiday.is_holiday(d):
                    dates.append(d)
        d += timedelta(days=1)

    # 追加日付（翌月の日付など）
    for extra_str in (extra or []):
        extra_date = date.fromisoformat(extra_str)
        if extra_date not in excluded_set and extra_date not in dates:
            if not exclude_holidays or not jpholiday.is_holiday(extra_date):
                dates.append(extra_date)

    dates.sort()
    return dates


def get_target_saturdays(
    year: int,
    month: int,
    excluded: list[str] = None,
    extra: list[str] = None,
) -> list[date]:
    """指定月の土曜日を取得（祝日除外）— 既存 optimizer.py の互換ラッパー"""
    return get_target_dates(
        year, month, days_of_week=[5],
        excluded=excluded, extra=extra,
        exclude_holidays=True,
    )


def get_weekday_target_dates(
    year: int,
    month: int,
    days_of_week: list[int],
) -> list[date]:
    """指定月の平日対象日を取得（祝日除外なし）"""
    return get_target_dates(
        year, month, days_of_week=days_of_week,
        exclude_holidays=False,
    )


# ---- 制約チェック ----

def is_ng_date(doctor_id: int, date_str: str, preferences: list[dict]) -> bool:
    """医員のNG日かどうかをチェック"""
    for pref in preferences:
        if pref.get("doctor_id") == doctor_id:
            return date_str in (pref.get("ng_dates") or [])
    return False


def is_avoid_date(doctor_id: int, date_str: str, preferences: list[dict]) -> bool:
    """医員の避けたい日かどうかをチェック"""
    for pref in preferences:
        if pref.get("doctor_id") == doctor_id:
            return date_str in (pref.get("avoid_dates") or [])
    return False


def validate_assignment(
    doctor_id: int,
    date_str: str,
    preferences: list[dict],
) -> tuple[bool, str]:
    """割り当てのバリデーション（ハード制約チェック）

    Returns:
        (is_valid, error_message)
    """
    if is_ng_date(doctor_id, date_str, preferences):
        return False, "NG日に割り当てられています"
    return True, ""


def check_soft_constraints(
    doctor_id: int,
    date_str: str,
    preferences: list[dict],
) -> list[str]:
    """ソフト制約の警告チェック

    Returns:
        警告メッセージのリスト（空なら問題なし）
    """
    warnings = []
    if is_avoid_date(doctor_id, date_str, preferences):
        warnings.append("避けたい日に割り当てられています")
    return warnings


# ---- 基本最適化（平日用） ----

def solve_weekday_schedule(
    target_dates: list[date],
    slots: list[dict],
    doctors: list[dict],
    preferences: list[dict],
) -> dict | None:
    """平日スケジュールの自動割り当て

    NG日をハード制約、避けたい日をソフト制約として、
    各医員の割り当て回数を均一化するPuLP最適化。

    Args:
        target_dates: 対象日リスト
        slots: スロット定義リスト [{id, slot_name, day_of_week, required_count, ...}]
        doctors: 医員リスト [{id, name, ...}]
        preferences: 希望リスト [{doctor_id, ng_dates, avoid_dates, ...}]

    Returns:
        {date_str: {slot_id: [doctor_id, ...]}} or None (infeasible)
    """
    if not target_dates or not slots or not doctors:
        return None

    doc_ids = [d["id"] for d in doctors]

    # 各日付に適用されるスロットを特定（曜日ベース）
    date_slots = {}
    for dt in target_dates:
        dow = dt.weekday()
        applicable = [s for s in slots if s.get("day_of_week") == dow and s.get("is_active", 1)]
        if applicable:
            date_slots[dt] = applicable

    if not date_slots:
        return None

    # 希望のルックアップ
    pref_map = {}
    for p in preferences:
        pref_map[p["doctor_id"]] = p

    # PuLP問題定義
    prob = pulp.LpProblem("weekday_schedule", pulp.LpMinimize)

    # 決定変数: x[doc_id, date, slot_id] ∈ {0, 1}
    x = {}
    for dt, dt_slots in date_slots.items():
        for slot in dt_slots:
            for doc_id in doc_ids:
                key = (doc_id, dt.isoformat(), slot["id"])
                x[key] = pulp.LpVariable(f"x_{doc_id}_{dt.isoformat()}_{slot['id']}", cat="Binary")

    # ---- ハード制約 ----

    # 1. 各スロットにrequired_count人
    for dt, dt_slots in date_slots.items():
        for slot in dt_slots:
            req = int(slot.get("required_count", 1))
            available = []
            for doc_id in doc_ids:
                key = (doc_id, dt.isoformat(), slot["id"])
                if key in x:
                    available.append(x[key])
            if available:
                prob += pulp.lpSum(available) == req, f"slot_fill_{dt.isoformat()}_{slot['id']}"

    # 2. NG日除外
    for doc_id in doc_ids:
        pref = pref_map.get(doc_id, {})
        ng = set(pref.get("ng_dates") or [])
        for dt, dt_slots in date_slots.items():
            if dt.isoformat() in ng:
                for slot in dt_slots:
                    key = (doc_id, dt.isoformat(), slot["id"])
                    if key in x:
                        prob += x[key] == 0, f"ng_{doc_id}_{dt.isoformat()}_{slot['id']}"

    # 3. 各医員は1日に最大1スロット
    for dt, dt_slots in date_slots.items():
        for doc_id in doc_ids:
            day_vars = []
            for slot in dt_slots:
                key = (doc_id, dt.isoformat(), slot["id"])
                if key in x:
                    day_vars.append(x[key])
            if len(day_vars) > 1:
                prob += pulp.lpSum(day_vars) <= 1, f"one_per_day_{doc_id}_{dt.isoformat()}"

    # ---- 目標関数 ----

    # 各医員の合計割り当て回数
    count_vars = {}
    for doc_id in doc_ids:
        doc_total = []
        for dt, dt_slots in date_slots.items():
            for slot in dt_slots:
                key = (doc_id, dt.isoformat(), slot["id"])
                if key in x:
                    doc_total.append(x[key])
        count_vars[doc_id] = pulp.lpSum(doc_total) if doc_total else 0

    # 頻度均一化: 各医員の割り当て回数の分散を最小化
    total_slots_needed = sum(
        int(slot.get("required_count", 1))
        for dt_slots in date_slots.values()
        for slot in dt_slots
    )
    n_docs = len(doc_ids)
    avg_count = total_slots_needed / n_docs if n_docs > 0 else 0

    # 偏差変数
    dev_plus = {}
    dev_minus = {}
    for doc_id in doc_ids:
        dev_plus[doc_id] = pulp.LpVariable(f"dev_p_{doc_id}", lowBound=0)
        dev_minus[doc_id] = pulp.LpVariable(f"dev_m_{doc_id}", lowBound=0)
        prob += count_vars[doc_id] - avg_count == dev_plus[doc_id] - dev_minus[doc_id], \
            f"dev_{doc_id}"

    # 避けたい日ペナルティ
    avoid_penalty = []
    for doc_id in doc_ids:
        pref = pref_map.get(doc_id, {})
        avoid = set(pref.get("avoid_dates") or [])
        for dt, dt_slots in date_slots.items():
            if dt.isoformat() in avoid:
                for slot in dt_slots:
                    key = (doc_id, dt.isoformat(), slot["id"])
                    if key in x:
                        avoid_penalty.append(x[key])

    # 目標: 頻度分散最小化（重み10） + 避けたい日ペナルティ（重み1）
    prob += (
        10 * pulp.lpSum(dev_plus[d] + dev_minus[d] for d in doc_ids)
        + pulp.lpSum(avoid_penalty)
    )

    # 求解
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=30))

    if prob.status != pulp.constants.LpStatusOptimal:
        return None

    # 結果を構築
    result = {}
    for dt, dt_slots in date_slots.items():
        ds = dt.isoformat()
        result[ds] = {}
        for slot in dt_slots:
            assigned = []
            for doc_id in doc_ids:
                key = (doc_id, ds, slot["id"])
                if key in x and pulp.value(x[key]) > 0.5:
                    assigned.append(doc_id)
            result[ds][slot["id"]] = assigned

    return result
