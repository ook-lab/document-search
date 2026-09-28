"""Gemini API 等の一時的エラー（HTTP 503 UNAVAILABLE）判定ユーティリティ。"""
from __future__ import annotations

import re
from typing import Any

TEMPORARY_503_PREFIX = "[TEMPORARY_503] "


def is_gemini_503_error(err: Any) -> bool:
    """Gemini API や Google 側の一時的不調（HTTP 503 UNAVAILABLE 等）由来のエラーかを判定する。

    例外オブジェクトのステータスコード/属性、原因例外、エラーメッセージ文字列のいずれからでも判定可能。
    """
    if err is None:
        return False

    # 1. 印のチェック
    if isinstance(err, str) and "[TEMPORARY_503]" in err:
        return True

    # 2. 例外オブジェクトの属性・原因例外の検査
    if isinstance(err, BaseException):
        # 属性チェック (code, status_code, http_status)
        for attr in ("code", "status_code", "http_status"):
            val = getattr(err, attr, None)
            if val == 503 or val == "503":
                return True
        # response 属性
        resp = getattr(err, "response", None)
        if resp is not None:
            sc = getattr(resp, "status_code", None) or getattr(resp, "status", None)
            if sc == 503 or sc == "503":
                return True
        # 原因例外の再帰的検査
        cause = getattr(err, "__cause__", None)
        if cause is not None and is_gemini_503_error(cause):
            return True
        ctx = getattr(err, "__context__", None)
        if ctx is not None and is_gemini_503_error(ctx):
            return True

    # 3. 辞書型の場合（API レスポンス辞書など）
    if isinstance(err, dict):
        if err.get("code") == 503 or err.get("status") == "UNAVAILABLE":
            return True
        error_dict = err.get("error")
        if isinstance(error_dict, dict):
            if error_dict.get("code") == 503 or error_dict.get("status") == "UNAVAILABLE":
                return True

    # 4. 文字列表現のパターン検査
    s = str(err)
    if "[TEMPORARY_503]" in s:
        return True

    # 代表的な 503 エラーパターン
    if "Authentication backend unavailable" in s:
        return True
    if "The service is currently unavailable" in s:
        return True
    if "503 UNAVAILABLE" in s or "503 Unavailable" in s:
        return True
    if "503 Service Unavailable" in s or "503 Server Error" in s:
        return True
    if "'code': 503" in s or '"code": 503' in s or "code: 503" in s:
        return True

    # 正規表現: 503 かつ (unavailable | backend | service)
    if re.search(r"\b503\b", s) and re.search(r"(unavailable|backend|service)", s, re.IGNORECASE):
        return True

    return False


def format_503_error(err: Any) -> str:
    """エラーメッセージを記録用にフォーマットする。

    503 由来のエラーの場合は先頭に [TEMPORARY_503] を付与する（既にあれば二重付与しない）。
    """
    err_str = str(err) if err is not None else ""
    if is_gemini_503_error(err):
        if not err_str.startswith("[TEMPORARY_503]"):
            return f"{TEMPORARY_503_PREFIX}{err_str}"
    return err_str
