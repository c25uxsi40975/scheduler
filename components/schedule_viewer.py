"""スケジュール画像ビューア

st.image() でインライン表示し、st.html() から親DOMにフルスクリーン
オーバーレイを注入する。画像タップでフルスクリーン表示、ピンチ拡大対応。
"""
import base64

import streamlit as st

from components.schedule_image import generate_schedule_image, generate_schedule_pdf


def render_schedule_with_viewer(sched, doctors, clinics, target_month):
    """スケジュール画像をビューア付きで表示する。

    - インライン画像表示（st.image）
    - 画像タップ → フルスクリーンオーバーレイ（ピンチ拡大対応）
    - PDFダウンロードボタン
    """
    img_data = generate_schedule_image(sched, doctors, clinics, target_month)
    if not img_data:
        return

    st.image(img_data, use_container_width=True)

    pdf_data = generate_schedule_pdf(sched, doctors, clinics, target_month)
    img_b64 = base64.b64encode(img_data).decode()

    pdf_btn_html = ""
    pdf_btn_js = ""
    if pdf_data:
        pdf_b64 = base64.b64encode(pdf_data).decode()
        pdf_btn_html = '<button class="sv-btn" id="sv-pdf-btn">PDFを保存</button>'
        pdf_btn_js = f"""
        document.getElementById("sv-pdf-btn").addEventListener("click", function() {{
            var bin = atob("{pdf_b64}");
            var u8 = new Uint8Array(bin.length);
            for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
            var blob = new Blob([u8], {{type: "application/pdf"}});
            var a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = "schedule_{target_month}.pdf";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }});
        """

    html = f"""
    <style>
    .sv-btn {{
        padding: 8px 16px; background: #f0f2f6; border: 1px solid #d0d0d0;
        border-radius: 6px; cursor: pointer; font-size: 14px; color: #262730;
        font-family: -apple-system, sans-serif;
    }}
    .sv-actions {{ display: flex; gap: 10px; padding: 4px 0; }}
    </style>
    <div class="sv-actions">
        {pdf_btn_html}
    </div>
    <script>
    (function() {{
        /* ---- PDF download (iframe internal) ---- */
        {pdf_btn_js}

        /* ---- Fullscreen overlay (parent DOM injection) ---- */
        var pDoc;
        try {{ pDoc = window.parent.document; pDoc.body; }} catch(e) {{ return; }}

        var OID = "sv-fullscreen-overlay";
        /* Remove stale overlay from previous Streamlit reruns */
        var old = pDoc.getElementById(OID);
        if (old) old.remove();

        /* Build overlay */
        var overlay = pDoc.createElement("div");
        overlay.id = OID;
        overlay.style.cssText = "display:none;position:fixed;top:0;left:0;width:100%;height:100%;"
            + "background:rgba(0,0,0,0.95);z-index:999999;overflow:hidden;"
            + "touch-action:none;user-select:none;-webkit-user-select:none;";

        /* Close button */
        var closeBtn = pDoc.createElement("button");
        closeBtn.textContent = "\\u2715 \\u9589\\u3058\\u308b";
        closeBtn.style.cssText = "position:fixed;top:12px;right:16px;z-index:1000000;"
            + "background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);"
            + "color:#fff;font-size:16px;padding:10px 18px;border-radius:8px;cursor:pointer;"
            + "font-family:-apple-system,sans-serif;backdrop-filter:blur(4px);";
        closeBtn.addEventListener("click", function() {{ overlay.style.display = "none"; resetZoom(); }});
        overlay.appendChild(closeBtn);

        /* Image container */
        var imgWrap = pDoc.createElement("div");
        imgWrap.style.cssText = "width:100%;height:100%;display:flex;align-items:center;"
            + "justify-content:center;overflow:hidden;";
        var fsImg = pDoc.createElement("img");
        fsImg.src = "data:image/png;base64,{img_b64}";
        fsImg.style.cssText = "max-width:100%;max-height:100%;object-fit:contain;"
            + "transform-origin:0 0;will-change:transform;";
        imgWrap.appendChild(fsImg);
        overlay.appendChild(imgWrap);

        /* Close on background tap (not on image or close button) */
        overlay.addEventListener("click", function(e) {{
            if (e.target === overlay || e.target === imgWrap) {{
                overlay.style.display = "none";
                resetZoom();
            }}
        }});

        pDoc.body.appendChild(overlay);

        /* ---- Pinch-zoom & pan state ---- */
        var scale = 1, lastScale = 1;
        var posX = 0, posY = 0, lastPosX = 0, lastPosY = 0;
        var startDist = 0;
        var startMidX = 0, startMidY = 0;
        var panStartX = 0, panStartY = 0;

        function resetZoom() {{
            scale = 1; lastScale = 1;
            posX = 0; posY = 0; lastPosX = 0; lastPosY = 0;
            fsImg.style.transform = "";
        }}

        function applyTransform() {{
            fsImg.style.transform = "translate(" + posX + "px," + posY + "px) scale(" + scale + ")";
        }}

        function dist(t) {{
            return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
        }}

        imgWrap.addEventListener("touchstart", function(e) {{
            if (e.touches.length === 2) {{
                e.preventDefault();
                startDist = dist(e.touches);
                lastScale = scale;
                startMidX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
                startMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
                lastPosX = posX;
                lastPosY = posY;
            }} else if (e.touches.length === 1 && scale > 1) {{
                panStartX = e.touches[0].clientX - posX;
                panStartY = e.touches[0].clientY - posY;
            }}
        }}, {{ passive: false }});

        imgWrap.addEventListener("touchmove", function(e) {{
            e.preventDefault();
            if (e.touches.length === 2) {{
                var d = dist(e.touches);
                scale = Math.min(Math.max(1, lastScale * (d / startDist)), 6);
                var midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
                var midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
                posX = lastPosX + (midX - startMidX);
                posY = lastPosY + (midY - startMidY);
                applyTransform();
            }} else if (e.touches.length === 1 && scale > 1) {{
                posX = e.touches[0].clientX - panStartX;
                posY = e.touches[0].clientY - panStartY;
                applyTransform();
            }}
        }}, {{ passive: false }});

        imgWrap.addEventListener("touchend", function(e) {{
            if (e.touches.length === 0) {{
                lastScale = scale;
                lastPosX = posX;
                lastPosY = posY;
                if (scale <= 1.05) resetZoom();
            }} else if (e.touches.length === 1 && scale > 1) {{
                panStartX = e.touches[0].clientX - posX;
                panStartY = e.touches[0].clientY - posY;
            }}
        }});

        /* Double-tap to toggle zoom */
        var lastTap = 0;
        imgWrap.addEventListener("touchend", function(e) {{
            if (e.touches.length > 0) return;
            var now = Date.now();
            if (now - lastTap < 300) {{
                if (scale > 1.05) {{
                    resetZoom();
                }} else {{
                    scale = 3; lastScale = 3;
                    var rect = fsImg.getBoundingClientRect();
                    var cx = e.changedTouches[0].clientX;
                    var cy = e.changedTouches[0].clientY;
                    posX = -(cx - rect.left) * 2;
                    posY = -(cy - rect.top) * 2;
                    lastPosX = posX; lastPosY = posY;
                    applyTransform();
                }}
                lastTap = 0;
            }} else {{
                lastTap = now;
            }}
        }});

        /* ---- Attach click to st.image() in parent DOM ---- */
        function showOverlay() {{
            overlay.style.display = "block";
        }}

        /* st.image renders as: <div data-testid="stImage"><img ...></div> */
        var attempts = 0;
        function attachClick() {{
            var imgs = pDoc.querySelectorAll('[data-testid="stImage"] img');
            if (imgs.length > 0) {{
                /* Use the last image found (most likely ours) */
                var target = imgs[imgs.length - 1];
                if (!target.dataset.svClickAttached) {{
                    target.style.cursor = "zoom-in";
                    target.addEventListener("click", showOverlay);
                    target.dataset.svClickAttached = "1";
                }}
            }} else if (attempts < 20) {{
                attempts++;
                setTimeout(attachClick, 200);
            }}
        }}
        attachClick();
    }})();
    </script>
    """
    st.html(html)
