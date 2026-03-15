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
    """指定月の平日対象日を取得（祝日除外）"""
    return get_target_dates(
        year, month, days_of_week=days_of_week,
        exclude_holidays=True,
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
    slot_overrides: dict = None,
    fixed_assignments: dict = None,
) -> dict | None:
    """平日スケジュールの自動割り当て

    NG日をハード制約、避けたい日をソフト制約として、
    各医員の割り当て回数を均一化するPuLP最適化。

    Args:
        target_dates: 対象日リスト
        slots: スロット定義リスト [{id, slot_name, day_of_week, required_count, ...}]
        doctors: 医員リスト [{id, name, ...}]
        preferences: 希望リスト [{doctor_id, ng_dates, avoid_dates, ...}]
        slot_overrides: 日別オーバーライド {(slot_id, date_str): required_count}
                        0=休診, 他=人数
        fixed_assignments: 固定済みアサイン {date_str: {slot_id: [doctor_id, ...]}}
                           補填モードで使用。指定された割り当てをハード制約として固定し、
                           残りの空きスロットのみ最適化する。

    Returns:
        {date_str: {slot_id: [doctor_id, ...]}} or None (infeasible)

    Raises:
        ValueError: 人数不足で割り当て不可能な場合（詳細メッセージ付き）
    """
    if not target_dates or not slots or not doctors:
        return None

    slot_overrides = slot_overrides or {}
    fixed_assignments = fixed_assignments or {}
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

    # 固定済みアサインのルックアップ（補填モード用）
    fixed_set = set()  # (doc_id, date_str, slot_id)
    for ds, slots_map in fixed_assignments.items():
        for sid, dids in slots_map.items():
            sid_int = int(sid) if isinstance(sid, str) else sid
            for did in dids:
                fixed_set.add((did, ds, sid_int))

    # NG日を考慮した事前チェック: 各日×スロットで利用可能人数 >= 必要人数か
    shortage_details = []
    for dt, dt_slots in date_slots.items():
        ds = dt.isoformat()
        for slot in dt_slots:
            ovr_req = slot_overrides.get((slot["id"], ds))
            if ovr_req is not None and ovr_req == 0:
                continue
            req = ovr_req if ovr_req is not None else int(slot.get("required_count", 1))
            # 固定済み人数を差し引き
            fixed_count = sum(1 for did, fds, fsid in fixed_set
                              if fds == ds and fsid == slot["id"])
            remaining_req = req - fixed_count
            if remaining_req <= 0:
                continue  # 固定だけで充足
            # 固定済み医員を除いた利用可能人数をチェック
            fixed_on_day = {did for did, fds, _ in fixed_set if fds == ds}
            available_docs = []
            ng_docs = []
            for doc_id in doc_ids:
                if doc_id in fixed_on_day:
                    continue  # 固定済み医員は空き枠の候補から除外
                pref = pref_map.get(doc_id, {})
                ng = set(pref.get("ng_dates") or [])
                if ds in ng:
                    ng_docs.append(doc_id)
                else:
                    available_docs.append(doc_id)
            if len(available_docs) < remaining_req:
                ng_names = [next((d["name"] for d in doctors if d["id"] == did), str(did))
                            for did in ng_docs]
                shortage_details.append(
                    f"  {dt.strftime('%m/%d(%a)')} {slot.get('slot_name', '')}: "
                    f"必要{remaining_req}人 / 利用可能{len(available_docs)}人 "
                    f"（NG: {', '.join(ng_names)}）"
                )
    if shortage_details:
        raise ValueError(
            "NG日により人数不足のため生成できません:\n" + "\n".join(shortage_details)
        )

    # PuLP問題定義
    prob = pulp.LpProblem("weekday_schedule", pulp.LpMinimize)

    # 決定変数: x[doc_id, date, slot_id] ∈ {0, 1}
    x = {}
    for dt, dt_slots in date_slots.items():
        for slot in dt_slots:
            # 休診オーバーライドのスロットは変数を作らない
            ovr_req = slot_overrides.get((slot["id"], dt.isoformat()))
            if ovr_req is not None and ovr_req == 0:
                continue
            for doc_id in doc_ids:
                key = (doc_id, dt.isoformat(), slot["id"])
                x[key] = pulp.LpVariable(f"x_{doc_id}_{dt.isoformat()}_{slot['id']}", cat="Binary")

    # ---- ハード制約 ----

    # 1. 各スロットにrequired_count人（オーバーライド優先）
    for dt, dt_slots in date_slots.items():
        for slot in dt_slots:
            ovr_req = slot_overrides.get((slot["id"], dt.isoformat()))
            if ovr_req is not None:
                if ovr_req == 0:
                    continue  # 休診: 変数なし → 制約不要
                req = ovr_req
            else:
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

    # 4. 固定アサイン（補填モード）: 指定された割り当てを強制
    for did, ds, sid in fixed_set:
        key = (did, ds, sid)
        if key in x:
            prob += x[key] == 1, f"fixed_{did}_{ds}_{sid}"

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

    # 頻度均一化: 各医員の割り当て回数の分散を最小化（オーバーライド考慮）
    total_slots_needed = 0
    for dt, dt_slots in date_slots.items():
        for slot in dt_slots:
            ovr_req = slot_overrides.get((slot["id"], dt.isoformat()))
            if ovr_req is not None:
                total_slots_needed += ovr_req  # 0=休診なら加算なし
            else:
                total_slots_needed += int(slot.get("required_count", 1))
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

    # 月別均一化: 各月内でも割り当てが偏らないようにする
    from collections import defaultdict
    month_dates = defaultdict(list)
    for dt in date_slots:
        month_dates[dt.strftime("%Y-%m")].append(dt)

    monthly_dev_plus = {}
    monthly_dev_minus = {}
    if len(month_dates) > 1:
        for ym, m_dates in month_dates.items():
            # この月の必要スロット数を算出
            month_total = 0
            for dt in m_dates:
                for slot in date_slots[dt]:
                    ovr_req = slot_overrides.get((slot["id"], dt.isoformat()))
                    if ovr_req is not None:
                        month_total += ovr_req
                    else:
                        month_total += int(slot.get("required_count", 1))
            month_avg = month_total / n_docs if n_docs > 0 else 0

            for doc_id in doc_ids:
                doc_month_total = []
                for dt in m_dates:
                    for slot in date_slots[dt]:
                        key = (doc_id, dt.isoformat(), slot["id"])
                        if key in x:
                            doc_month_total.append(x[key])
                m_count = pulp.lpSum(doc_month_total) if doc_month_total else 0
                dp = pulp.LpVariable(f"mdev_p_{doc_id}_{ym}", lowBound=0)
                dm = pulp.LpVariable(f"mdev_m_{doc_id}_{ym}", lowBound=0)
                prob += m_count - month_avg == dp - dm, f"mdev_{doc_id}_{ym}"
                monthly_dev_plus[(doc_id, ym)] = dp
                monthly_dev_minus[(doc_id, ym)] = dm

    # 週内重複ペナルティ: 同じ週に同じ医員が複数回入るのを抑制
    from collections import defaultdict as _defaultdict
    week_dates = _defaultdict(list)
    for dt in date_slots:
        # ISO週番号でグループ化
        week_dates[dt.isocalendar()[:2]].append(dt)

    week_penalty = []
    for week_key, w_dates in week_dates.items():
        if len(w_dates) <= 1:
            continue
        for doc_id in doc_ids:
            week_vars = []
            for dt in w_dates:
                for slot in date_slots[dt]:
                    key = (doc_id, dt.isoformat(), slot["id"])
                    if key in x:
                        week_vars.append(x[key])
            if len(week_vars) > 1:
                # 週内合計が2以上ならペナルティ（超過分のみ）
                wp = pulp.LpVariable(f"wpen_{doc_id}_{week_key[0]}_{week_key[1]}", lowBound=0)
                prob += pulp.lpSum(week_vars) - 1 <= wp, \
                    f"week_over_{doc_id}_{week_key[0]}_{week_key[1]}"
                week_penalty.append(wp)

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

    # 目標: 期間全体の均一化（重み10） + 月別均一化（重み5）
    #        + 週内重複回避（重み3） + 避けたい日ペナルティ（重み1）
    prob += (
        10 * pulp.lpSum(dev_plus[d] + dev_minus[d] for d in doc_ids)
        + 5 * pulp.lpSum(monthly_dev_plus[k] + monthly_dev_minus[k]
                         for k in monthly_dev_plus)
        + 3 * pulp.lpSum(week_penalty)
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
