"""平日外勤の年間アサイン数サマリ

副管理者・医員のスケジュール確認タブで共通利用。
年度（4月〜翌3月）または暦年（1月〜12月）単位で、医員ごと・月ごとのアサイン数を表示する。
"""
from datetime import date

import streamlit as st

from components.display_utils import build_display_name_map

PERIOD_FISCAL = "年度（4月〜翌3月）"
PERIOD_CALENDAR = "暦年（1月〜12月）"


def period_months(year: int, period_type: str) -> list[str]:
    """期間に含まれる12ヶ月を "YYYY-MM" で返す"""
    if period_type == PERIOD_FISCAL:
        months = [(year, m) for m in range(4, 13)] + [(year + 1, m) for m in range(1, 4)]
    else:
        months = [(year, m) for m in range(1, 13)]
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
                          confirmed_only: bool = False,
                          highlight_doctor_id: int | None = None,
                          key_prefix: str = "wk_yearly"):
    """年間アサイン数を表示

    Args:
        section: 平日セクション
        all_doctors: 全医員リスト（表示名解決用）
        member_ids: 表に必ず載せる医員ID（セクション所属メンバー）
        confirmed_only: True なら確定済み月のみ集計（医員画面用）
        highlight_doctor_id: 自分の行を強調表示する医員ID
        key_prefix: Streamlit ウィジェットキーの接頭辞
    """
    import pandas as pd
    from database import get_weekday_schedules_for_months, get_weekday_confirmed_months

    today = date.today()
    c1, c2 = st.columns(2)
    with c1:
        period_type = st.radio(
            "集計単位", [PERIOD_FISCAL, PERIOD_CALENDAR],
            horizontal=True, key=f"{key_prefix}_ptype_{section}",
        )
    current_year = (today.year if today.month >= 4 else today.year - 1) \
        if period_type == PERIOD_FISCAL else today.year
    years = list(range(current_year + 1, current_year - 5, -1))
    with c2:
        year = st.selectbox(
            "年度" if period_type == PERIOD_FISCAL else "年",
            years, index=1,
            format_func=lambda y: f"{y}年度" if period_type == PERIOD_FISCAL else f"{y}年",
            key=f"{key_prefix}_year_{section}_{period_type}",
        )

    months = period_months(year, period_type)
    confirmed = set(get_weekday_confirmed_months(section))
    target_months = [ym for ym in months if ym in confirmed] if confirmed_only else months

    schedules = get_weekday_schedules_for_months(section, tuple(target_months))
    counts = count_assignments(schedules, target_months)

    if not counts:
        st.info("この期間のアサインはまだありません。")
        return

    doc_map = build_display_name_map(all_doctors)
    # 所属メンバー＋期間中にアサインのあった医員（異動者も含める）
    row_ids = list(dict.fromkeys(list(member_ids) + sorted(counts.keys())))

    totals = {did: sum(counts.get(did, {}).values()) for did in row_ids}
    n = len([did for did in row_ids if did in member_ids]) or len(row_ids)
    grand_total = sum(totals.values())
    avg = grand_total / n if n else 0

    if highlight_doctor_id is not None:
        my_total = totals.get(highlight_doctor_id, 0)
        m1, m2 = st.columns(2)
        m1.metric("あなたの年間アサイン数", f"{my_total}回")
        m2.metric("メンバー平均", f"{avg:.1f}回", delta=f"{my_total - avg:+.1f}",
                  delta_color="off")
    else:
        st.caption(f"期間合計: {grand_total}回　メンバー平均: {avg:.1f}回/人")

    # 表示する月列: スケジュールが存在する月のみ（未作成月は空列になるため省略）
    shown_months = [ym for ym in target_months if ym in schedules]

    rows = []
    for did in sorted(row_ids, key=lambda d: (-totals[d], doc_map.get(d, ""))):
        name = doc_map.get(did, str(did))
        if did == highlight_doctor_id:
            name = f"★ {name}"
        if did not in member_ids:
            name = f"{name}（現在メンバー外）"
        row = {"医員": name, "年間合計": totals[did]}
        for ym in shown_months:
            row[_month_label(ym)] = counts.get(did, {}).get(ym, 0)
        rows.append(row)
    total_row = {"医員": "合計", "年間合計": grand_total}
    for ym in shown_months:
        total_row[_month_label(ym)] = sum(counts.get(d, {}).get(ym, 0) for d in row_ids)
    rows.append(total_row)

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    notes = ["年末年始（12/29〜1/3）は集計対象外"]
    if confirmed_only:
        notes.append("確定済みの月のみ集計")
    else:
        unconfirmed = [_month_label(ym) for ym in shown_months if ym not in confirmed]
        if unconfirmed:
            notes.append(f"未確定の月を含みます（{'・'.join(unconfirmed)}）")
    st.caption("※ " + "／".join(notes))
