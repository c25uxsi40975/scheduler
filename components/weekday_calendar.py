"""平日外勤セクションのカレンダー表示コンポーネント

月間カレンダーグリッドで全メンバーのスケジュールを表示する。
副管理者・医員のスケジュール確認タブで共通利用。
"""
import calendar
from datetime import date
import streamlit as st

from components.display_utils import build_display_name_map


def render_weekday_calendar(schedule: list, slots: list, view_month: str,
                            all_doctors: list, highlight_doctor_id: int | None = None):
    """月間カレンダーグリッドで全メンバーのスケジュールを表示

    Args:
        schedule: get_weekday_schedule() の結果
        slots: get_weekday_slots() の結果
        view_month: "YYYY-MM"
        all_doctors: 全医員リスト
        highlight_doctor_id: ハイライトする医員ID（Noneなら全員同色）
    """
    year, month = map(int, view_month.split("-"))
    doc_map = build_display_name_map(all_doctors)

    # スロット情報マップ
    slot_map = {s["id"]: s for s in slots}

    # スケジュールを日付→スロット→医員名のマップに変換
    day_data = {}  # {date_str: {slot_id: [doctor_ids]}}
    for r in schedule:
        ds = r["date"]
        sid = r["slot_id"]
        day_data.setdefault(ds, {}).setdefault(sid, []).append(r["doctor_id"])

    cal = calendar.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)

    # 色定義
    MEMBER_COLORS = [
        "#e3f2fd", "#fff3e0", "#e8f5e9", "#fce4ec", "#e0f7fa",
        "#f3e5f5", "#fff9c4", "#dcedc8", "#ffe0b2", "#b2ebf2",
    ]
    # 医員ごとに色を割り当て
    unique_doc_ids = sorted(set(r["doctor_id"] for r in schedule))
    doc_color_map = {did: MEMBER_COLORS[i % len(MEMBER_COLORS)]
                     for i, did in enumerate(unique_doc_ids)}

    # HTML生成
    html = '<table style="width:100%; border-collapse:collapse; font-size:0.85rem; table-layout:fixed;">'
    # ヘッダー
    html += '<tr>'
    for day_name in ["月", "火", "水", "木", "金", "土", "日"]:
        bg = "#e3f2fd" if day_name == "土" else "#fce4ec" if day_name == "日" else "#f5f5f5"
        html += (f'<th style="border:1px solid #ddd; padding:4px; text-align:center; '
                 f'background:{bg};">{day_name}</th>')
    html += '</tr>'

    for week in month_days:
        html += '<tr>'
        for day in week:
            if day == 0:
                html += '<td style="border:1px solid #eee; padding:4px; height:80px;">&nbsp;</td>'
            else:
                ds = date(year, month, day).isoformat()
                entries = day_data.get(ds, {})
                cell_content = f'<div style="font-weight:bold; margin-bottom:2px;">{day}</div>'

                if entries:
                    for sid, doc_ids in sorted(entries.items()):
                        slot = slot_map.get(sid, {})
                        slot_name = slot.get("slot_name", "")
                        if slot_name and len(entries) > 1:
                            cell_content += (
                                f'<div style="font-size:0.65rem; color:#888; '
                                f'margin-top:1px;">{slot_name}</div>'
                            )
                        for did in doc_ids:
                            name = doc_map.get(did, str(did))
                            if highlight_doctor_id and did == highlight_doctor_id:
                                bg = "#ffeb3b"
                                fw = "bold"
                            else:
                                bg = doc_color_map.get(did, "#f5f5f5")
                                fw = "normal"
                            cell_content += (
                                f'<div style="background:{bg}; border-radius:3px; '
                                f'padding:1px 3px; margin:1px 0; font-size:0.75rem; '
                                f'font-weight:{fw}; overflow:hidden; text-overflow:ellipsis; '
                                f'white-space:nowrap;">{name}</div>'
                            )

                td_bg = ""
                if highlight_doctor_id and any(
                    highlight_doctor_id in dids
                    for dids in entries.values()
                ):
                    td_bg = "background:#fffde7;"
                html += (f'<td style="border:1px solid #ddd; padding:4px; '
                         f'vertical-align:top; height:80px; overflow:hidden; {td_bg}">'
                         f'{cell_content}</td>')
        html += '</tr>'
    html += '</table>'

    st.markdown(html, unsafe_allow_html=True)

    # 凡例
    if highlight_doctor_id:
        st.caption("黄色背景: あなたの割り当て")
