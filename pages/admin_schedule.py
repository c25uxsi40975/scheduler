"""管理者: 確定スケジュール確認タブ"""
import streamlit as st
from database import get_doctors, get_clinics, get_schedules
from components.schedule_table import render_schedule_table


def render(target_month):
    st.header(f"確定スケジュール ({target_month})")

    schedules = get_schedules(target_month)
    confirmed = [s for s in schedules if s["is_confirmed"]]

    if confirmed:
        sched = confirmed[0]
        df = render_schedule_table(sched, get_doctors(), get_clinics())

        # CSV出力
        if df is not None:
            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "CSVダウンロード",
                csv,
                file_name=f"gaikin_schedule_{target_month}.csv",
                mime="text/csv"
            )
    else:
        st.info("確定済みのスケジュールはまだありません")
