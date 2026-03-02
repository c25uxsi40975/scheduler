"""スケジュール画像ビューア

st.image() でインライン表示し、画像タップで Streamlit 内蔵の
フルスクリーン表示をトリガーする。

NOTE: st.html() は DOMPurify で <script> を除去するため使用不可。
st.components.v1.html() は iframe (sandbox: allow-same-origin + allow-scripts)
で実行されるため、window.parent.document 経由で親DOMにアクセスできる。
"""
import streamlit as st
import streamlit.components.v1 as components

from components.schedule_image import generate_schedule_image


def render_schedule_with_viewer(sched, doctors, clinics, target_month):
    """スケジュール画像をビューア付きで表示する。

    - インライン画像表示（st.image）
    - 画像タップ → Streamlit 内蔵フルスクリーン表示
    """
    img_data = generate_schedule_image(sched, doctors, clinics, target_month)
    if not img_data:
        return

    st.image(img_data, use_container_width=True)

    # 画像タップで Streamlit のフルスクリーンボタンをクリックさせる
    # st.components.v1.html() は iframe 内で実行され、
    # sandbox に allow-same-origin があるため window.parent.document にアクセス可能
    components.html("""
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

            /* Use the last stImage (most likely ours) */
            var container = containers[containers.length - 1];
            var img = container.querySelector('img');
            if (!img || img.dataset.tapFullscreen) return;
            img.dataset.tapFullscreen = '1';
            img.style.cursor = 'zoom-in';

            img.addEventListener('click', function() {
                /*
                 * Streamlit toolbar button is NOT inside stImage directly.
                 * It's in a parent wrapper element. Walk up the DOM to find it.
                 */
                var btn = null;
                var el = container;
                for (var i = 0; i < 6 && el && !btn; i++) {
                    btn = el.querySelector('button[data-testid="StyledFullScreenButton"]')
                        || el.querySelector('[data-testid="stElementToolbar"] button');
                    el = el.parentElement;
                }
                if (btn) btn.click();
            });
        }
        setup();
    })();
    </script>
    """, height=0)
