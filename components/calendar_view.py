"""
全体スケジュールカレンダーコンポーネント
土曜＋平日の全割り当てを統合表示
"""
import calendar
from datetime import date
from dateutil.relativedelta import relativedelta
import streamlit as st

from database import (
    get_weekday_configs,
    get_weekday_schedule,
    get_schedules,
    get_confirmed_months,
)


def render(doctor: dict):
    """月間カレンダーグリッドで全セクションのスケジュールを統合表示"""
    st.subheader("全体スケジュール")

    today = date.today()
    months = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(-1, 4)]
    view_month = st.selectbox("月を選択", months, key="cal_view_month")

    year, month = map(int, view_month.split("-"))

    # 土曜スケジュール取得
    saturday_entries = _get_saturday_entries(doctor, view_month)

    # 平日スケジュール取得
    weekday_entries = _get_weekday_entries(doctor, view_month)

    # カレンダー描画
    _render_calendar_grid(year, month, saturday_entries, weekday_entries, doctor)


def _get_saturday_entries(doctor: dict, year_month: str) -> dict:
    """土曜スケジュールから医員の割り当てを取得

    Returns:
        {date_str: [{"clinic": clinic_name, "section": "saturday"}]}
    """
    entries = {}
    try:
        schedules = get_schedules(year_month)
        for sched in schedules:
            if not sched.get("is_confirmed"):
                continue
            assignments = sched.get("assignments", {})
            for date_str, clinics in assignments.items():
                for clinic_name, doc_ids in clinics.items():
                    if doctor["id"] in doc_ids:
                        if date_str not in entries:
                            entries[date_str] = []
                        entries[date_str].append({
                            "clinic": clinic_name,
                            "section": "saturday",
                            "color": "#e3f2fd",
                        })
    except Exception:
        pass
    return entries


def _get_weekday_entries(doctor: dict, year_month: str) -> dict:
    """平日スケジュールから医員の割り当てを取得

    Returns:
        {date_str: [{"clinic": clinic_name, "slot": slot_name, "section": section}]}
    """
    entries = {}
    SECTION_COLORS = ["#fff3e0", "#e8f5e9", "#fce4ec", "#e0f7fa", "#f3e5f5"]

    try:
        configs = get_weekday_configs()
        for i, cfg in enumerate(configs):
            if not cfg.get("is_active"):
                continue
            section = cfg["section"]
            clinic_name = cfg["clinic_name"]
            color = SECTION_COLORS[i % len(SECTION_COLORS)]

            schedule = get_weekday_schedule(year_month, section)
            for r in schedule:
                if r["doctor_id"] == doctor["id"]:
                    ds = r["date"]
                    if ds not in entries:
                        entries[ds] = []
                    entries[ds].append({
                        "clinic": clinic_name,
                        "slot": r.get("slot_name", ""),
                        "section": section,
                        "color": color,
                    })
    except Exception:
        pass
    return entries


def _render_calendar_grid(year: int, month: int,
                          saturday_entries: dict, weekday_entries: dict,
                          doctor: dict):
    """月間カレンダーをHTML tableで描画"""
    cal = calendar.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)

    # マージ
    all_entries = {}
    for ds, elist in saturday_entries.items():
        all_entries.setdefault(ds, []).extend(elist)
    for ds, elist in weekday_entries.items():
        all_entries.setdefault(ds, []).extend(elist)

    # HTML生成
    html = '<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
    html += '<tr>'
    for day_name in ["月", "火", "水", "木", "金", "土", "日"]:
        bg = "#e3f2fd" if day_name == "土" else "#fce4ec" if day_name == "日" else "#f5f5f5"
        html += f'<th style="border:1px solid #ddd; padding:4px; text-align:center; background:{bg};">{day_name}</th>'
    html += '</tr>'

    for week in month_days:
        html += '<tr>'
        for day in week:
            if day == 0:
                html += '<td style="border:1px solid #eee; padding:4px;">&nbsp;</td>'
            else:
                ds = date(year, month, day).isoformat()
                entries = all_entries.get(ds, [])
                cell_content = f'<div style="font-weight:bold; margin-bottom:2px;">{day}</div>'
                for e in entries:
                    bg = e.get("color", "#f5f5f5")
                    label = e["clinic"]
                    if e.get("slot"):
                        label += f' ({e["slot"]})'
                    cell_content += (
                        f'<div style="background:{bg}; border-radius:3px; '
                        f'padding:1px 3px; margin:1px 0; font-size:0.75rem; '
                        f'white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">'
                        f'{label}</div>'
                    )
                td_style = "border:1px solid #ddd; padding:4px; vertical-align:top; min-height:60px;"
                if entries:
                    td_style += " background:#fffde7;"
                html += f'<td style="{td_style}">{cell_content}</td>'
        html += '</tr>'

    html += '</table>'

    st.markdown(html, unsafe_allow_html=True)

    # 凡例
    st.caption("背景色付き: あなたの割り当て")
