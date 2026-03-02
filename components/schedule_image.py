"""スケジュール表の画像生成コンポーネント

スプレッドシート風のスケジュール画像を生成する。
上部: 外勤先×日付テーブル（外勤先が列、日付が行）
下部: 医員×日付テーブル（日付が列、医員が行）
"""
import io
import os
from datetime import date

from PIL import Image, ImageDraw, ImageFont


# 日本語フォント検索パス
_FONT_PATHS_REGULAR = [
    # WSL / Windows
    "/mnt/c/Windows/Fonts/YuGothM.ttc",
    "/mnt/c/Windows/Fonts/YuGothR.ttc",
    "/mnt/c/Windows/Fonts/meiryo.ttc",
    "/mnt/c/Windows/Fonts/msgothic.ttc",
    # Linux (Streamlit Cloud / Debian / Ubuntu)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKjp-Regular.otf",
]

_FONT_PATHS_BOLD = [
    "/mnt/c/Windows/Fonts/YuGothB.ttc",
    "/mnt/c/Windows/Fonts/meiryob.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKjp-Bold.otf",
]


def _find_cjk_font():
    """fc-list でシステム上の CJK フォントパスを動的に検索"""
    import subprocess
    try:
        out = subprocess.run(
            ["fc-list", ":lang=ja", "file"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            path = line.split(":")[0].strip()
            if path and os.path.exists(path):
                return path
    except Exception:
        pass
    return None


def _load_font(size, bold=False):
    """日本語フォントを検索してロード"""
    candidates = _FONT_PATHS_BOLD if bold else _FONT_PATHS_REGULAR
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Bold が見つからない場合は Regular にフォールバック
    if bold:
        return _load_font(size, bold=False)
    # fc-list で動的検索
    fallback = _find_cjk_font()
    if fallback:
        try:
            return ImageFont.truetype(fallback, size)
        except Exception:
            pass
    return None


def _text_size(draw, text, font):
    """テキストの描画サイズ (幅, 高さ) を取得"""
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _build_schedule_image(sched, doctors, clinics, year_month,
                          highlight_doctor_id=None):
    """スケジュールの PIL Image オブジェクトを生成する（内部共通関数）。

    Returns:
        Image | None: PIL Image オブジェクト
    """
    from components.display_utils import build_display_name_map
    doc_map = build_display_name_map(doctors)
    clinic_map = {c["id"]: c["name"] for c in clinics}

    # ---- 割り当てデータ構築 ----
    cal_data = {}   # {date_str: {clinic_name: doctor_name}}
    doc_sched = {}  # {doctor_id: {date_str: clinic_name}}

    for a in sched["assignments"]:
        ds = a["date"]
        cname = clinic_map.get(a["clinic_id"], "?")
        dname = doc_map.get(a["doctor_id"], "?")
        cal_data.setdefault(ds, {})[cname] = dname
        doc_sched.setdefault(a["doctor_id"], {})[ds] = cname

    if not cal_data:
        return None

    dates_sorted = sorted(cal_data.keys())

    # 外勤先マスタの登録順を保持
    clinic_order = {c["name"]: i for i, c in enumerate(clinics)}
    all_clinic_names = sorted(
        {cn for day_data in cal_data.values() for cn in day_data},
        key=lambda cn: clinic_order.get(cn, 999),
    )

    day_labels = [str(date.fromisoformat(ds).day) for ds in dates_sorted]
    _, month_str = year_month.split("-")
    month_label = f"{int(month_str)}月"

    # ハイライト対象の医員表示名
    hl_doc_name = doc_map.get(highlight_doctor_id) if highlight_doctor_id else None

    # ---- テーブルデータ構築 ----
    # 上部テーブル: 外勤先×日付
    top_header = [month_label] + all_clinic_names
    top_rows = []
    top_hl_cells = set()  # ハイライトするセル (row, col)
    for i, ds in enumerate(dates_sorted):
        row = [day_labels[i]]
        for ci, cn in enumerate(all_clinic_names):
            val = cal_data[ds].get(cn, "×")
            row.append(val)
            if hl_doc_name and val == hl_doc_name:
                top_hl_cells.add((i, ci + 1))  # +1 for day label column
        top_rows.append(row)

    # 下部テーブル: 医員×日付
    doc_sorted = sorted(
        doctors, key=lambda x: (x.get("account", ""), x["name"])
    )
    bot_header = [""] + day_labels
    bot_rows = []
    highlight_row = None  # ハイライト対象の行インデックス
    for idx, d in enumerate(doc_sorted):
        row = [doc_map.get(d["id"], d["name"])]
        for ds in dates_sorted:
            row.append(doc_sched.get(d["id"], {}).get(ds, ""))
        bot_rows.append(row)
        if highlight_doctor_id and d["id"] == highlight_doctor_id:
            highlight_row = idx

    # ---- フォント読み込み ----
    font_size = 16
    font = _load_font(font_size, bold=False)
    bold_font = _load_font(font_size, bold=True)
    if font is None:
        return None  # 日本語フォントが見つからない
    if bold_font is None:
        bold_font = font

    # ---- セルサイズ計算 ----
    pad_x, pad_y = 10, 6
    tmp = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(tmp)

    _, sample_h = _text_size(draw, "あ", bold_font)
    cell_h = sample_h + pad_y * 2 + 4
    min_col_w = 40

    def calc_col_widths(header, rows):
        widths = []
        for ci in range(len(header)):
            max_w = 0
            # ヘッダーは常に太字
            if header[ci]:
                w, _ = _text_size(draw, header[ci], bold_font)
                max_w = w
            # データ列: col 0 は太字、他は通常
            f = bold_font if ci == 0 else font
            for r in rows:
                if ci < len(r) and r[ci]:
                    w, _ = _text_size(draw, r[ci], f)
                    max_w = max(max_w, w)
            widths.append(max(max_w + pad_x * 2, min_col_w))
        return widths

    top_cw = calc_col_widths(top_header, top_rows)
    bot_cw = calc_col_widths(bot_header, bot_rows)

    # ---- 画像サイズ ----
    margin = 4
    top_tw = sum(top_cw)
    bot_tw = sum(bot_cw)
    img_w = max(top_tw, bot_tw) + margin * 2

    top_th = (1 + len(top_rows)) * cell_h
    gap = cell_h * 2
    bot_th = (1 + len(bot_rows)) * cell_h
    img_h = margin + top_th + gap + bot_th + margin

    # ---- 描画 ----
    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    line_color = (180, 180, 180)
    header_bg = (242, 242, 242)
    highlight_bg = (255, 255, 200)  # 薄い黄色

    def draw_cell_text(x, y, w, text, f):
        """セル内にテキストを中央揃えで描画"""
        if not text:
            return
        tw, th = _text_size(draw, text, f)
        tx = x + (w - tw) // 2
        ty = y + (cell_h - th) // 2
        draw.text((tx, ty), text, fill=(0, 0, 0), font=f)

    def draw_table(x0, y0, col_ws, header, rows, hl_row=None,
                   hl_cells=None):
        """テーブルを描画"""
        tw = sum(col_ws)
        n_rows = 1 + len(rows)

        # ヘッダー行背景
        draw.rectangle(
            [x0, y0, x0 + tw, y0 + cell_h],
            fill=header_bg,
        )

        # ハイライト行背景
        if hl_row is not None:
            hy = y0 + (hl_row + 1) * cell_h
            draw.rectangle(
                [x0, hy, x0 + tw, hy + cell_h],
                fill=highlight_bg,
            )

        # ハイライトセル背景
        if hl_cells:
            for (ri, ci) in hl_cells:
                cx = x0 + sum(col_ws[:ci])
                cy = y0 + (ri + 1) * cell_h
                draw.rectangle(
                    [cx, cy, cx + col_ws[ci], cy + cell_h],
                    fill=highlight_bg,
                )

        # 横罫線
        for r in range(n_rows + 1):
            y = y0 + r * cell_h
            draw.line([(x0, y), (x0 + tw, y)], fill=line_color)

        # 縦罫線
        cx = x0
        for cw in col_ws:
            draw.line([(cx, y0), (cx, y0 + n_rows * cell_h)], fill=line_color)
            cx += cw
        draw.line([(cx, y0), (cx, y0 + n_rows * cell_h)], fill=line_color)

        # ヘッダーテキスト（太字）
        cx = x0
        for ci, text in enumerate(header):
            draw_cell_text(cx, y0, col_ws[ci], text, bold_font)
            cx += col_ws[ci]

        # データ行
        for ri, row in enumerate(rows):
            y = y0 + (ri + 1) * cell_h
            cx = x0
            for ci, text in enumerate(row):
                f = bold_font if ci == 0 else font
                draw_cell_text(cx, y, col_ws[ci], text, f)
                cx += col_ws[ci]

    # 上部テーブル描画
    draw_table(margin, margin, top_cw, top_header, top_rows,
               hl_cells=top_hl_cells)

    # 下部テーブル描画
    bot_y = margin + top_th + gap
    draw_table(margin, bot_y, bot_cw, bot_header, bot_rows, hl_row=highlight_row)

    return img


def generate_schedule_image(sched, doctors, clinics, year_month,
                            highlight_doctor_id=None):
    """スケジュールをスプレッドシート風の PNG 画像として生成する。

    Returns:
        bytes | None: PNG 画像データ
    """
    img = _build_schedule_image(sched, doctors, clinics, year_month,
                                highlight_doctor_id=highlight_doctor_id)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def generate_schedule_pdf(sched, doctors, clinics, year_month):
    """スケジュールを PDF として生成する。

    Returns:
        bytes | None: PDF データ
    """
    img = _build_schedule_image(sched, doctors, clinics, year_month)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    buf.seek(0)
    return buf.getvalue()
