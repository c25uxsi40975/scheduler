"""
全医員パスワード強制リセットスクリプト

全医員のパスワードを固定の初期パスワードにリセットし、bcryptハッシュで保存する。

使い方:
  streamlit run scripts/reset_all_passwords.py

注意:
  - 実行前にバックアップを推奨
  - 管理者パスワードはリセットされません（管理者パスワードは別途設定してください）
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from database import init_db, get_doctors
from database.auth import set_doctor_individual_password

DEFAULT_PASSWORD = "aaaa1111"

st.set_page_config(page_title="パスワード一括リセット", layout="wide")
st.title("全医員パスワード一括リセット")

init_db()

doctors = get_doctors(active_only=False)

if not doctors:
    st.warning("医員が登録されていません")
    st.stop()

st.info(f"対象医員数: {len(doctors)} 名")
st.info(f"初期パスワード: `{DEFAULT_PASSWORD}`")
st.warning("実行すると全医員のパスワードがリセットされます。")

if st.button("全医員のパスワードをリセット", type="primary"):
    progress = st.progress(0)

    for i, doc in enumerate(doctors):
        set_doctor_individual_password(doc["id"], DEFAULT_PASSWORD)
        progress.progress((i + 1) / len(doctors))

    st.success(f"{len(doctors)} 名のパスワードを `{DEFAULT_PASSWORD}` にリセットしました")
