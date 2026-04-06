"""Google Drive ユーティリティ

スケジュール画像等を Drive にアップロードし公開URLを取得する。
認証は gspread と同じサービスアカウントを使用。
"""
import json
import logging

import requests
import streamlit as st
from google.auth.transport.requests import Request as AuthRequest
from google.oauth2.service_account import Credentials

_log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"


def _get_drive_token() -> str:
    """サービスアカウントの access token を取得"""
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=_SCOPES,
    )
    creds.refresh(AuthRequest())
    return creds.token


def _find_existing_file(token: str, filename: str, folder_id: str | None = None) -> str | None:
    """同名ファイルが既にあれば file_id を返す"""
    q = f"name='{filename}' and trashed=false"
    if folder_id:
        q += f" and '{folder_id}' in parents"
    resp = requests.get(
        _FILES_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"q": q, "fields": "files(id)", "spaces": "drive"},
        timeout=10,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    return files[0]["id"] if files else None


def _delete_file(token: str, file_id: str):
    """Drive ファイルを削除"""
    resp = requests.delete(
        f"{_FILES_URL}/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()


def _get_folder_id() -> str | None:
    """Secrets から Drive フォルダ ID を取得"""
    return st.secrets.get("schedule_image_folder_id", None)


def upload_schedule_image(png_bytes: bytes, filename: str) -> str | None:
    """PNG 画像を Google Drive にアップロードし、公開設定にして file_id を返す。

    Secrets に schedule_image_folder_id が設定されていればそのフォルダに保存。
    同名ファイルが既にあれば削除してから再アップロードする。
    失敗時は None を返す。
    """
    if not png_bytes:
        return None

    try:
        token = _get_drive_token()
        folder_id = _get_folder_id()

        # 既存ファイルを削除
        existing_id = _find_existing_file(token, filename, folder_id)
        if existing_id:
            _delete_file(token, existing_id)

        # multipart upload
        meta = {"name": filename, "mimeType": "image/png"}
        if folder_id:
            meta["parents"] = [folder_id]
        metadata = json.dumps(meta)

        resp = requests.post(
            _UPLOAD_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"uploadType": "multipart", "fields": "id"},
            files={
                "metadata": ("metadata", metadata, "application/json"),
                "file": (filename, png_bytes, "image/png"),
            },
            timeout=30,
        )
        resp.raise_for_status()
        file_id = resp.json()["id"]

        # 公開設定 (anyone with link can view)
        requests.post(
            f"{_FILES_URL}/{file_id}/permissions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"type": "anyone", "role": "reader"},
            timeout=10,
        ).raise_for_status()

        _log.info("Drive アップロード完了: %s (id=%s, folder=%s)", filename, file_id, folder_id)
        return file_id

    except Exception:
        _log.warning("Drive アップロード失敗: %s", filename, exc_info=True)
        return None
