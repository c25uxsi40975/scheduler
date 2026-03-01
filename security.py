"""
セキュリティユーティリティ
パスワードポリシー・レートリミッター・入力検証・一時パスワード生成
"""
import re
import time
import secrets
import string
import streamlit as st


# ---- パスワード生成 ----

def generate_temp_password(length: int = 12) -> str:
    """ランダムな一時パスワードを生成（英数字）"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_reset_code(length: int = 6) -> str:
    """数字のみのリセットコードを生成"""
    return "".join(secrets.choice(string.digits) for _ in range(length))


# ---- パスワードポリシー ----

def validate_password(password: str) -> tuple[bool, str]:
    """パスワードポリシーを検証。(有効, エラーメッセージ) を返す。"""
    if len(password) < 8:
        return False, "パスワードは8文字以上にしてください"
    if password.isdigit():
        return False, "パスワードに英字を含めてください"
    if password.isalpha():
        return False, "パスワードに数字を含めてください"
    return True, ""


# ---- メール検証 ----

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def validate_email(email: str) -> bool:
    """メールアドレスのフォーマットを検証。空文字は許可。"""
    if not email:
        return True
    return bool(_EMAIL_PATTERN.match(email))


# ---- レートリミッター ----

_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300  # 5分


def check_rate_limit(key: str = "login") -> tuple[bool, int]:
    """ログイン試行回数を確認。(許可, 残りロックアウト秒数) を返す。"""
    lockout_key = f"_rate_limit_{key}_lockout_until"
    lockout_until = st.session_state.get(lockout_key, 0)
    now = time.time()

    if now < lockout_until:
        return False, int(lockout_until - now)

    # ロックアウト期限切れならリセット
    if lockout_until > 0:
        st.session_state[f"_rate_limit_{key}_attempts"] = 0
        st.session_state[lockout_key] = 0

    return True, 0


def record_failed_attempt(key: str = "login"):
    """ログイン失敗を記録。閾値を超えたらロックアウト。"""
    attempts_key = f"_rate_limit_{key}_attempts"
    lockout_key = f"_rate_limit_{key}_lockout_until"

    attempts = st.session_state.get(attempts_key, 0) + 1
    st.session_state[attempts_key] = attempts

    if attempts >= _MAX_ATTEMPTS:
        st.session_state[lockout_key] = time.time() + _LOCKOUT_SECONDS


def reset_rate_limit(key: str = "login"):
    """ログイン成功時にカウンターをリセット。"""
    st.session_state.pop(f"_rate_limit_{key}_attempts", None)
    st.session_state.pop(f"_rate_limit_{key}_lockout_until", None)
