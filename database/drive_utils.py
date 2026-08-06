"""Google Drive ユーティリティ

スケジュール画像等を GAS Web App 経由で Drive にアップロードする。

NOTE: サービスアカウントにはストレージ割り当てがないため、
GAS (スクリプトオーナーの権限) を通してアップロードする。
"""
import base64
import logging

import requests
import streamlit as st

from .connection import is_readonly

_log = logging.getLogger(__name__)


def _get_folder_id() -> str | None:
    """Secrets から Drive フォルダ ID を取得"""
    return st.secrets.get("schedule_image_folder_id", None)


def upload_schedule_image(png_bytes: bytes, filename: str) -> str | None:
    """PNG 画像を GAS 経由で Google Drive にアップロードし file_id を返す。

    失敗時は None を返す。
    """
    if not png_bytes:
        return None

    if is_readonly():
        _log.info("読取専用モード: Drive アップロードをスキップしました")
        return None

    gas_url = st.secrets.get("gas_webapp_url", "")
    if not gas_url:
        _log.warning("gas_webapp_url が未設定です")
        return None

    try:
        payload = {
            "action": "upload_drive_image",
            "image_base64": base64.b64encode(png_bytes).decode(),
            "filename": filename,
        }
        folder_id = _get_folder_id()
        if folder_id:
            payload["folder_id"] = folder_id

        resp = requests.post(gas_url, json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()

        if result.get("status") == "ok":
            file_id = result["file_id"]
            _log.info("Drive アップロード完了: %s (id=%s)", filename, file_id)
            return file_id

        _log.warning("Drive アップロード失敗: %s - %s", filename, result.get("message"))
        return None

    except Exception:
        _log.warning("Drive アップロード失敗: %s", filename, exc_info=True)
        return None
