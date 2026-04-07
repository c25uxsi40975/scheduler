"""Google Drive ユーティリティ

スケジュール画像等を共有ドライブにアップロードし公開URLを取得する。
認証は gspread と同じサービスアカウントを使用。

NOTE: サービスアカウントにはストレージ割り当てがないため、
アップロード先は必ず共有ドライブ（Shared Drive）上のフォルダにする。
"""
import logging

import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

_log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_drive_service():
    """サービスアカウントで認証済み Drive サービスを取得"""
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=_SCOPES,
    )
    return build("drive", "v3", credentials=creds)


def _find_existing_file(service, filename: str, folder_id: str | None = None) -> str | None:
    """同名ファイルが既にあれば file_id を返す"""
    q = f"name='{filename}' and trashed=false"
    if folder_id:
        q += f" and '{folder_id}' in parents"
    resp = (
        service.files()
        .list(
            q=q,
            fields="files(id)",
            spaces="drive",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


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
        service = _get_drive_service()
        folder_id = _get_folder_id()

        # 既存ファイルを削除
        existing_id = _find_existing_file(service, filename, folder_id)
        if existing_id:
            service.files().delete(
                fileId=existing_id, supportsAllDrives=True
            ).execute()

        # アップロード (共有ドライブ対応)
        meta = {"name": filename, "mimeType": "image/png"}
        if folder_id:
            meta["parents"] = [folder_id]

        media = MediaInMemoryUpload(png_bytes, mimetype="image/png")
        created = (
            service.files()
            .create(
                body=meta,
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        file_id = created["id"]

        # 公開設定 (anyone with link can view)
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()

        _log.info("Drive アップロード完了: %s (id=%s, folder=%s)", filename, file_id, folder_id)
        return file_id

    except Exception:
        _log.warning("Drive アップロード失敗: %s", filename, exc_info=True)
        return None
