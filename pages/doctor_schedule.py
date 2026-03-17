"""医員: スケジュール確認タブ"""
import streamlit as st
from datetime import date
from database import get_doctors, get_clinics, get_schedules
from components.schedule_viewer import render_schedule_with_viewer


def render(doctor, target_month):
    if not st.session_state.get("doctor_authenticated"):
        st.stop()
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
            # 同一日の割り当てをグループ化
            from collections import defaultdict
            day_groups = defaultdict(list)
            for a in my_assignments:
                day_groups[a["date"]].append(a)

            for ds in sorted(day_groups.keys()):
                group = day_groups[ds]
                d_obj = date.fromisoformat(ds)
                day_label = d_obj.strftime('%m/%d(%a)')
                if len(group) >= 2:
                    names = " / ".join(
                        clinic_map.get(a["clinic_id"], "?") for a in group
                    )
                    st.write(f"**{day_label}** → {names}")
                    st.caption(f"　この日は2か所の掛け持ちです")
                else:
                    cname = clinic_map.get(group[0]["clinic_id"], "?")
                    st.write(f"**{day_label}** → {cname}")
        else:
            st.info("今月の外勤割り当てはありません")

        # 全体スケジュール（フルスクリーンビューア付き）
        st.markdown("---")
        st.subheader("全体スケジュール")
        render_schedule_with_viewer(sched, doctors, clinics, target_month,
                                    highlight_doctor_id=doctor["id"])
    else:
        st.info("まだスケジュールが確定されていません")
