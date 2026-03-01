"""管理者: 確定スケジュール確認タブ"""
import base64
import streamlit as st
from database import get_doctors, get_clinics, get_schedules
from components.schedule_table import render_schedule_table, render_doctor_view_table, render_doctor_stats_table
from components.schedule_image import generate_schedule_image, generate_schedule_pdf


def _render_open_links(img_data, pdf_data):
    """拡大表示・PDF表示リンクを提供する（新しいタブで開く）"""
    img_b64 = base64.b64encode(img_data).decode()
    pdf_js = ""
    pdf_link = ""
    if pdf_data:
        pdf_b64 = base64.b64encode(pdf_data).decode()
        pdf_link = '<a id="sched-pdf-link" href="#" target="_blank" rel="noopener">PDFを開く</a>'
        pdf_js = f"""
        var pdfBlob = b64toBlob("{pdf_b64}", "application/pdf");
        document.getElementById("sched-pdf-link").href = URL.createObjectURL(pdfBlob);
        """
    html = f"""
    <style>
    .sched-actions {{ display: flex; gap: 10px; padding: 4px 0; }}
    .sched-actions a {{
        padding: 8px 16px; background: #f0f2f6; border: 1px solid #d0d0d0;
        border-radius: 6px; text-decoration: none; color: #262730;
        font-size: 14px; font-family: -apple-system, sans-serif;
    }}
    </style>
    <div class="sched-actions">
        <a id="sched-zoom-link" href="#" target="_blank" rel="noopener">拡大表示</a>
        {pdf_link}
    </div>
    <script>
    (function() {{
        function b64toBlob(b64, mime) {{
            var bin = atob(b64);
            var u8 = new Uint8Array(bin.length);
            for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
            return new Blob([u8], {{type: mime}});
        }}
        var imgBlob = b64toBlob("{img_b64}", "image/png");
        document.getElementById("sched-zoom-link").href = URL.createObjectURL(imgBlob);
        {pdf_js}
    }})();
    </script>
    """
    st.html(html)


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
            pdf_data = generate_schedule_pdf(sched, doctors, clinics, target_month)
            _render_open_links(img_data, pdf_data)

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
