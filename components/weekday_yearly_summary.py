"""平日外勤の年間（年度）アサイン数サマリ

副管理者のスケジュール確認タブで利用。
年度（4月〜翌3月）単位で、医員ごと・月ごとのアサイン数を表示する。
"""
from datetime import date

import streamlit as st

from components.display_utils import build_display_name_map


def fiscal_year_of(d: date) -> int:
    """日付が属する年度（4月始まり）を返す"""
    return d.year if d.month >= 4 else d.year - 1


def fiscal_year_months(fy: int) -> list[str]:
    """年度に含まれる12ヶ月（4月〜翌3月）を "YYYY-MM" で返す"""
    months = [(fy, m) for m in range(4, 13)] + [(fy + 1, m) for m in range(1, 4)]
    return [f"{y:04d}-{m:02d}" for y, m in months]


def count_assignments(schedules_by_month: dict, months: list[str]) -> dict:
    """{doctor_id: {year_month: count}} を返す（年末年始は除外）"""
    from scheduling_utils import is_nenmatsu_nenshi

    counts: dict[int, dict[str, int]] = {}
    for ym in months:
        for r in schedules_by_month.get(ym, []):
            did = r.get("doctor_id")
            if not did:
                continue
            try:
                if is_nenmatsu_nenshi(date.fromisoformat(r["date"])):
                    continue
            except (ValueError, KeyError):
                continue
            counts.setdefault(did, {}).setdefault(ym, 0)
            counts[did][ym] += 1
    return counts


def _month_label(ym: str) -> str:
    return f"{int(ym[5:7])}月"


def render_yearly_summary(section: str, all_doctors: list, member_ids: list,
                          key_prefix: str = "wk_yearly"):
    """年度アサイン数（医員×月）を表示"""
    import pandas as pd
    from database import get_weekday_schedules_for_months, get_weekday_confirmed_months

    current_fy = fiscal_year_of(date.today())
    years = list(range(current_fy + 1, current_fy - 5, -1))
    fy = st.selectbox(
        "年度", years, index=years.index(current_fy),
        format_func=lambda y: f"{y}年度（{y}年4月〜{y + 1}年3月）",
        key=f"{key_prefix}_fy_{section}",
    )

    months = fiscal_year_months(fy)
    schedules = get_weekday_schedules_for_months(section, tuple(months))
    counts = count_assignments(schedules, months)

    if not counts:
        st.info(f"{fy}年度のアサインはまだありません。")
        return

    doc_map = build_display_name_map(all_doctors)
    # 所属メンバー＋年度中にアサインのあった医員（異動者も含める）
    row_ids = list(dict.fromkeys(list(member_ids) + sorted(counts.keys())))
    totals = {did: sum(counts.get(did, {}).values()) for did in row_ids}
    n_members = len([did for did in row_ids if did in member_ids]) or len(row_ids)
    grand_total = sum(totals.values())
    avg = grand_total / n_members if n_members else 0

    st.caption(f"{fy}年度合計: {grand_total}回　メンバー平均: {avg:.1f}回/人")

    # 列はスケジュールが存在する月のみ
    shown_months = [ym for ym in months if ym in schedules]

    rows = []
    for did in sorted(row_ids, key=lambda d: (-totals[d], doc_map.get(d, ""))):
        name = doc_map.get(did, str(did))
        if did not in member_ids:
            name = f"{name}（現在メンバー外）"
        row = {"医員": name, "年度合計": totals[did]}
        for ym in shown_months:
            row[_month_label(ym)] = counts.get(did, {}).get(ym, 0)
        rows.append(row)
    total_row = {"医員": "合計", "年度合計": grand_total}
    for ym in shown_months:
        total_row[_month_label(ym)] = sum(counts.get(d, {}).get(ym, 0) for d in row_ids)
    rows.append(total_row)

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    notes = ["年末年始（12/29〜1/3）は集計対象外"]
    confirmed = set(get_weekday_confirmed_months(section))
    unconfirmed = [_month_label(ym) for ym in shown_months if ym not in confirmed]
    if unconfirmed:
        notes.append(f"未確定の月を含みます（{'・'.join(unconfirmed)}）")
    st.caption("※ " + "／".join(notes))
