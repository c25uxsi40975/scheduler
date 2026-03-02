"""スケジュール画像ビューア

st.image() でインライン表示し、画像タップで親DOMにフルスクリーン
オーバーレイを注入。ピンチ操作で拡大・縮小が可能。

NOTE: st.html() は DOMPurify で <script> を除去するため使用不可。
st.components.v1.html() は iframe (sandbox: allow-same-origin + allow-scripts)
で実行されるため、window.parent.document 経由で親DOMにアクセスできる。
"""
import streamlit as st
import streamlit.components.v1 as components

from components.schedule_image import generate_schedule_image

_VIEWER_SCRIPT = """
<script>
(function() {
    var pDoc;
    try { pDoc = window.parent.document; pDoc.body; } catch(e) { return; }

    var attempts = 0;
    function setup() {
        var containers = pDoc.querySelectorAll('[data-testid="stImage"]');
        if (!containers.length) {
            if (attempts++ < 30) setTimeout(setup, 200);
            return;
        }
        var container = containers[containers.length - 1];
        var img = container.querySelector('img');
        if (!img || img.dataset.tapViewer) return;
        img.dataset.tapViewer = '1';
        img.style.cursor = 'zoom-in';

        img.addEventListener('click', function(e) {
            e.preventDefault();
            openViewer(img.src);
        });
    }

    function openViewer(src) {
        var old = pDoc.getElementById('__sv_ov');
        if (old) old.remove();

        /* --- Overlay --- */
        var ov = pDoc.createElement('div');
        ov.id = '__sv_ov';
        ov.style.cssText =
            'position:fixed;top:0;left:0;width:100vw;height:100vh;' +
            'background:rgba(0,0,0,0.95);z-index:999999;' +
            'display:flex;align-items:center;justify-content:center;' +
            'touch-action:none;user-select:none;-webkit-user-select:none;';

        /* --- Close button --- */
        var cb = pDoc.createElement('div');
        cb.innerHTML = '&times;';
        cb.style.cssText =
            'position:absolute;top:12px;right:12px;color:#fff;font-size:32px;' +
            'line-height:1;cursor:pointer;z-index:1000000;padding:4px 14px;' +
            'background:rgba(255,255,255,0.15);border-radius:50%;';
        cb.addEventListener('click', function() { close(); });

        /* --- Hint text --- */
        var hint = pDoc.createElement('div');
        hint.textContent = 'ピンチで拡大 / ダブルタップでリセット';
        hint.style.cssText =
            'position:absolute;bottom:16px;left:0;width:100%;text-align:center;' +
            'color:rgba(255,255,255,0.5);font-size:13px;z-index:1000000;' +
            'pointer-events:none;';

        /* --- Image --- */
        var vi = pDoc.createElement('img');
        vi.src = src;
        vi.style.cssText =
            'max-width:100%;max-height:100%;object-fit:contain;' +
            'transform-origin:0 0;will-change:transform;';

        ov.appendChild(cb);
        ov.appendChild(hint);
        ov.appendChild(vi);
        pDoc.body.appendChild(ov);

        /* --- Pinch-to-zoom state --- */
        var s = 1, px = 0, py = 0;   /* current transform */
        var ls, lpx, lpy;             /* saved at touchstart */
        var dist0, mid0;              /* initial pinch values */
        var pan0x, pan0y;             /* pan anchor */
        var pinching = false;
        var lastTap = 0;

        function D(a, b) {
            var dx = b.clientX - a.clientX, dy = b.clientY - a.clientY;
            return Math.sqrt(dx * dx + dy * dy);
        }
        function M(a, b) {
            return {x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2};
        }
        function apply() {
            vi.style.transform = 'translate(' + px + 'px,' + py + 'px) scale(' + s + ')';
        }

        ov.addEventListener('touchstart', function(e) {
            if (e.touches.length === 2) {
                e.preventDefault();
                pinching = true;
                dist0 = D(e.touches[0], e.touches[1]);
                mid0 = M(e.touches[0], e.touches[1]);
                ls = s; lpx = px; lpy = py;
            } else if (e.touches.length === 1) {
                var now = Date.now();
                if (now - lastTap < 300) {
                    /* Double-tap: reset zoom */
                    e.preventDefault();
                    s = 1; px = 0; py = 0; apply();
                    lastTap = 0;
                    return;
                }
                lastTap = now;
                pan0x = e.touches[0].clientX - px;
                pan0y = e.touches[0].clientY - py;
            }
        }, {passive: false});

        ov.addEventListener('touchmove', function(e) {
            e.preventDefault();
            if (e.touches.length === 2 && pinching) {
                var d = D(e.touches[0], e.touches[1]);
                var m = M(e.touches[0], e.touches[1]);
                var ns = Math.min(Math.max(ls * d / dist0, 1), 6);
                /* Keep pinch center stationary */
                px = m.x - (mid0.x - lpx) * (ns / ls);
                py = m.y - (mid0.y - lpy) * (ns / ls);
                s = ns;
                apply();
            } else if (e.touches.length === 1 && s > 1 && !pinching) {
                /* Pan when zoomed in */
                px = e.touches[0].clientX - pan0x;
                py = e.touches[0].clientY - pan0y;
                apply();
            }
        }, {passive: false});

        ov.addEventListener('touchend', function(e) {
            if (e.touches.length < 2) {
                pinching = false;
                ls = s; lpx = px; lpy = py;
            }
            if (s <= 1) { s = 1; px = 0; py = 0; apply(); }
        });

        /* Click overlay background to close (not image) */
        ov.addEventListener('click', function(e) {
            if (e.target === ov) close();
        });

        /* Escape key */
        function onKey(e) {
            if (e.key === 'Escape') close();
        }
        pDoc.addEventListener('keydown', onKey);

        function close() {
            ov.remove();
            pDoc.removeEventListener('keydown', onKey);
        }

        /* Auto-hide hint after 3 seconds */
        setTimeout(function() {
            hint.style.transition = 'opacity 0.5s';
            hint.style.opacity = '0';
        }, 3000);
    }

    setup();
})();
</script>
"""


def render_schedule_with_viewer(sched, doctors, clinics, target_month):
    """スケジュール画像をビューア付きで表示する。

    - インライン画像表示（st.image）
    - 画像タップ → フルスクリーンオーバーレイ（ピンチ拡大対応）
    """
    img_data = generate_schedule_image(sched, doctors, clinics, target_month)
    if not img_data:
        return

    st.image(img_data, use_container_width=True)

    # st.components.v1.html() は iframe 内で実行され、
    # sandbox に allow-same-origin があるため window.parent.document にアクセス可能
    components.html(_VIEWER_SCRIPT, height=0)
