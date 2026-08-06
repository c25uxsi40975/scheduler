"""医員の表示名ユーティリティ

名字が一意なら名字のみ表示、同姓がいればフルネーム表示にする。
last_name が未設定の既存医員は name（フルネーム）にフォールバック。
"""
from collections import Counter

import streamlit as st


def inject_master_css():
    """マスタ管理画面の共通CSS（行カラー + スマホ向けコンパクト化）を注入する。

    外勤先マスタ・医員マスタの両ページが render 冒頭で呼ぶ。
    """
    st.markdown("""<style>
    [data-testid="stVerticalBlockBorderWrapper"]:has(.row-active) {
        background-color: #e8f5e9 !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:has(.row-inactive) {
        background-color: #ffebee !important;
    }
    .row-active, .row-inactive { display: none; }

    /* スマホ向けコンパクト化 */
    @media (max-width: 768px) {
        .stMainBlockContainer { padding: 0.5rem !important; }
        h2 { font-size: 1.2rem !important; }
        h3 { font-size: 1rem !important; }
        p, .stMarkdown, .stText { font-size: 0.85rem !important; }
        .stButton > button {
            font-size: 0.75rem !important;
            padding: 0.2rem 0.5rem !important;
            min-height: 1.8rem !important;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            padding: 0.3rem !important;
        }
        [data-testid="stFormSubmitButton"] > button {
            font-size: 0.8rem !important;
        }
        .stRadio label { font-size: 0.8rem !important; }
        .stSelectbox label, .stTextInput label { font-size: 0.8rem !important; }
    }
    </style>""", unsafe_allow_html=True)


def build_display_name_map(doctors: list[dict]) -> dict[int, str]:
    """doctor_id → 表示名 のマップを構築する。

    - last_name が設定済みで一意 → 名字のみ（例: "田中"）
    - last_name が重複 → フルネーム（例: "田中太郎"）
    - last_name が未設定 → name フォールバック
    """
    # 各医員の「名字キー」を決定（last_name があればそれ、なければ name）
    last_names = {}
    for d in doctors:
        ln = d.get("last_name", "")
        last_names[d["id"]] = ln if ln else d.get("name", "")

    ln_counts = Counter(last_names.values())

    result = {}
    for d in doctors:
        ln = last_names[d["id"]]
        if ln_counts[ln] > 1:
            # 同姓がいる → フルネーム表示
            result[d["id"]] = d.get("name", ln)
        else:
            result[d["id"]] = ln
    return result


def build_reverse_display_name_map(doctors: list[dict]) -> dict[str, int]:
    """表示名 → doctor_id の逆引きマップ（手動調整セレクトボックス用）"""
    forward = build_display_name_map(doctors)
    return {name: did for did, name in forward.items()}
