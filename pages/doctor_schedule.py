"""医員: スケジュール確認タブ"""
import streamlit as st
from datetime import date
from database import get_doctors, get_clinics, get_schedules
from components.schedule_image import generate_schedule_image


def render(doctor, target_month):
    st.header(f"確定スケジュール ({target_month})")

    schedules = get_schedules(target_month)
    confirmed = [s for s in schedules if s["is_confirmed"]]

    if confirmed:
        sched = confirmed[0]
        doctors = get_doctors()
        clinics = get_clinics()
        clinic_map = {c["id"]: c["name"] for c in clinics}

        # 自分の担当だけハイライト
        my_assignments = [
            a for a in sched["assignments"]
            if a["doctor_id"] == doctor["id"]
        ]

        if my_assignments:
            st.subheader("あなたの外勤予定")
            for a in sorted(my_assignments, key=lambda x: x["date"]):
                d_obj = date.fromisoformat(a["date"])
                cname = clinic_map.get(a["clinic_id"], "?")
                st.write(f"**{d_obj.strftime('%m/%d(%a)')}** → {cname}")
        else:
            st.info("今月の外勤割り当てはありません")

        # 全体スケジュール（画像表示）
        st.markdown("---")
        st.subheader("全体スケジュール")
        img_data = generate_schedule_image(sched, doctors, clinics, target_month)
        if img_data:
            st.image(img_data, use_container_width=True)
            st.download_button(
                "画像をダウンロード",
                img_data,
                file_name=f"schedule_{target_month}.png",
                mime="image/png",
            )
        else:
            st.warning("スケジュール画像を生成できませんでした")
    else:
        st.info("まだスケジュールが確定されていません")
