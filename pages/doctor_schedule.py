"""医員: スケジュール確認タブ"""
import base64
import streamlit as st
from datetime import date
from database import get_doctors, get_clinics, get_schedules
from components.schedule_image import generate_schedule_image, generate_schedule_pdf


def _render_lightbox_image(img_data):
    """画像をタップで拡大可能なモーダル付きで表示する"""
    b64 = base64.b64encode(img_data).decode()
    html = f"""
    <style>
    .sched-img-wrap img {{
        width: 100%;
        cursor: zoom-in;
    }}
    .sched-overlay {{
        display: none;
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.85);
        z-index: 999999;
        justify-content: center;
        align-items: center;
        touch-action: pinch-zoom;
    }}
    .sched-overlay.active {{
        display: flex;
    }}
    .sched-overlay img {{
        max-width: 95vw;
        max-height: 90vh;
        object-fit: contain;
        touch-action: pinch-zoom;
    }}
    .sched-overlay .close-btn {{
        position: fixed;
        top: 12px; right: 16px;
        color: #fff;
        font-size: 36px;
        cursor: pointer;
        z-index: 1000000;
        line-height: 1;
        background: rgba(0,0,0,0.5);
        border-radius: 50%;
        width: 44px; height: 44px;
        display: flex;
        align-items: center;
        justify-content: center;
    }}
    </style>
    <div class="sched-img-wrap">
        <img src="data:image/png;base64,{b64}"
             onclick="this.parentElement.nextElementSibling.classList.add('active')" />
    </div>
    <div class="sched-overlay"
         onclick="if(event.target===this)this.classList.remove('active')">
        <span class="close-btn"
              onclick="this.parentElement.classList.remove('active')">&times;</span>
        <img src="data:image/png;base64,{b64}" />
    </div>
    """
    st.html(html)


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

        # 全体スケジュール（モーダル拡大対応）
        st.markdown("---")
        st.subheader("全体スケジュール")
        img_data = generate_schedule_image(sched, doctors, clinics, target_month)
        if img_data:
            _render_lightbox_image(img_data)

            # PDF ダウンロード
            pdf_data = generate_schedule_pdf(sched, doctors, clinics, target_month)
            if pdf_data:
                st.download_button(
                    "PDFをダウンロード",
                    pdf_data,
                    file_name=f"schedule_{target_month}.pdf",
                    mime="application/pdf",
                )
        else:
            st.warning("スケジュール画像を生成できませんでした")
    else:
        st.info("まだスケジュールが確定されていません")
