"""
AI使用量ログ記録ユーティリティ

AIトークン使用量をSupabaseに記録する。
失敗しても本処理に影響しない（fire-and-forget）。
"""
import uuid
from typing import Any, Optional

from loguru import logger


def nullable_uuid_str(value: Any) -> Optional[str]:
    """UUID 型カラム用。有効な UUID 文字列なら正規化して返し、それ以外は None（pipeline-lab の短い session_id 等）。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return str(uuid.UUID(s))
    except ValueError:
        return None


def log_ai_usage(
    app: str,
    stage: str,
    model: str,
    prompt_token_count: int = 0,
    candidates_token_count: int = 0,
    thoughts_token_count: int = 0,
    total_token_count: int = 0,
    session_id=None,
    workspace_id=None,
    metadata: dict = None,
) -> None:
    """AIトークン使用量をSupabaseに記録。失敗しても本処理に影響しない。"""
    try:
        from dms.common.database.client import DatabaseClient
        db = DatabaseClient(use_service_role=True)
        db.client.table('ai_usage_logs').insert({
            'app': app,
            'stage': stage,
            'model': model,
            'prompt_token_count': prompt_token_count,
            'candidates_token_count': candidates_token_count,
            'thoughts_token_count': thoughts_token_count,
            'total_token_count': total_token_count or (
                prompt_token_count + candidates_token_count + thoughts_token_count),
            'session_id': nullable_uuid_str(session_id),
            'workspace_id': str(workspace_id) if workspace_id else None,
            'metadata': metadata,
        }).execute()
    except Exception as e:
        logger.warning(f"AI cost logging failed (non-critical): {e}")
