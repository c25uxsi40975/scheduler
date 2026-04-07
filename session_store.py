"""ブラウザリフレッシュを跨いだセッション維持モジュール.

サーバーサイドに @st.cache_resource でセッションストアを保持し、
st.query_params のトークンで認証状態を復元する。
"""

import secrets
import time

import streamlit as st

_SESSION_TIMEOUT = 3600  # 1時間

_SESSION_KEYS = [
    "role",
    "admin_authenticated",
    "admin_type",
    "doctor_authenticated",
    "doctor_id",
    "doctor_section",
    "subadmin_doctor",
    "dev_authenticated",
    "dev_doctor_verified",
    "dev_doctor_id",
]


@st.cache_resource
def _get_store() -> dict:
    """サーバーサイドのセッションストア (token → snapshot)."""
    return {}


def save_session() -> None:
    """現在の session_state をスナップショットとして保存し、トークンを発行."""
    token = st.session_state.get("_session_token")
    if not token:
        token = secrets.token_urlsafe(32)
        st.session_state["_session_token"] = token

    store = _get_store()
    snapshot = {k: st.session_state.get(k) for k in _SESSION_KEYS}
    snapshot["last_activity"] = time.time()
    snapshot["created_at"] = store.get(token, {}).get("created_at", time.time())
    store[token] = snapshot

    st.query_params["_st"] = token


def restore_session() -> bool:
    """query_params のトークンからセッションを復元。成功時 True."""
    token = st.query_params.get("_st")
    if not token:
        return False

    store = _get_store()
    snapshot = store.get(token)
    if not snapshot:
        st.query_params.pop("_st", None)
        return False

    if time.time() - snapshot.get("last_activity", 0) > _SESSION_TIMEOUT:
        store.pop(token, None)
        st.query_params.pop("_st", None)
        return False

    for key in _SESSION_KEYS:
        if key in snapshot:
            st.session_state[key] = snapshot[key]
    st.session_state["_session_token"] = token
    st.session_state["_last_activity"] = time.time()
    snapshot["last_activity"] = time.time()
    return True


def clear_session() -> None:
    """セッションをストアとquery_paramsから削除."""
    token = st.session_state.pop("_session_token", None)
    if token:
        _get_store().pop(token, None)
    st.query_params.pop("_st", None)


def cleanup_expired() -> None:
    """期限切れセッションをストアから削除."""
    store = _get_store()
    now = time.time()
    expired = [k for k, v in store.items()
               if now - v.get("last_activity", 0) > _SESSION_TIMEOUT]
    for k in expired:
        del store[k]
