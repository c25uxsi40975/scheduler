"""管理者: 確定スケジュール確認タブ"""
import streamlit as st
from database import get_doctors, get_clinics, get_schedules
from components.schedule_table import render_schedule_table, render_doctor_view_table, render_doctor_stats_table
from components.schedule_image import generate_schedule_image


def render(target_month):
    st.header(f"確定スケジュール ({target_month})")

    schedules = get_schedules(target_month)
    confirmed = [s for s in schedules if s["is_confirmed"]]

    if confirmed:
        sched = confirmed[0]
        doctors = get_doctors()
        clinics = get_clinics()

        # スケジュール画像
        img_data = generate_schedule_image(sched, doctors, clinics, target_month)
        if img_data:
            st.image(img_data, use_container_width=True)
            st.download_button(
                "画像をダウンロード",
                img_data,
                file_name=f"schedule_{target_month}.png",
                mime="image/png",
            )

        # 詳細テーブル（折りたたみ）
        with st.expander("詳細テーブル表示"):
            df = render_schedule_table(sched, doctors, clinics)

            # 医員別ビュー
            render_doctor_view_table(sched, doctors)

            # 医員別統計
            render_doctor_stats_table(sched, doctors, clinics)

            # CSV出力
            if df is not None:
                csv = df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "CSVダウンロード",
                    csv,
                    file_name=f"gaikin_schedule_{target_month}.csv",
                    mime="text/csv",
                )
    else:
        st.info("確定済みのスケジュールはまだありません")
