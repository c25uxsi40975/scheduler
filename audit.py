"""
監査ログ — セキュリティイベントを Google Sheets に記録
"""
from datetime import datetime

from database.connection import (
    _retry, _ws_cache_master, _get_master_spreadsheet,
)

_AUDIT_HEADERS = ["timestamp", "event_type", "actor", "detail"]
_AUDIT_SHEET_NAME = "監査ログ"


def _get_audit_sheet():
    """監査ログシートを取得（なければ作成）"""
    if _AUDIT_SHEET_NAME in _ws_cache_master:
        return _ws_cache_master[_AUDIT_SHEET_NAME]
    sh = _get_master_spreadsheet()
    try:
        ws = _retry(sh.worksheet, _AUDIT_SHEET_NAME)
    except Exception:
        ws = sh.add_worksheet(
            title=_AUDIT_SHEET_NAME, rows=2000, cols=len(_AUDIT_HEADERS)
        )
        ws.update([_AUDIT_HEADERS], "A1")
    _ws_cache_master[_AUDIT_SHEET_NAME] = ws
    return ws


def log_event(event_type: str, actor: str = "", detail: str = ""):
    """監査ログに1行追記。アプリの動作を妨げないよう例外は握り潰す。"""
    try:
        ws = _get_audit_sheet()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _retry(ws.append_row, [now, event_type, actor, detail])
    except Exception:
        pass  # 監査ログの書き込み失敗でアプリを壊さない
