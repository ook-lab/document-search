"""ユーザーコンテキスト取得。

- 既定: user_context.yaml（後方互換）
- 優先: Supabase reading_context_profiles（person_name 指定時）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from supabase import create_client

from docsearch.config import settings


def _yaml_path() -> Path:
    # リポジトリ: services/doc-search/docsearch/user_context.yaml
    # Docker: /app/docsearch/user_context.yaml
    here = Path(__file__).resolve().parent
    return here / "user_context.yaml"


def load_user_context() -> Dict[str, Any]:
    path = _yaml_path()
    if not path.exists():
        return {"children": [], "settings": {}}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"children": [], "settings": {}}


def load_person_reading_context(person_name: Optional[str]) -> Dict[str, Any]:
    """reading_context_profiles から人別コンテキストを取得。無ければ空。"""
    name = (person_name or "").strip()
    if not name:
        return {"person_name": "", "title": "", "ai_payload_json": None, "ai_payload_md": ""}
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY or not settings.SUPABASE_ADMIN_USER_ID:
        return {"person_name": name, "title": "", "ai_payload_json": None, "ai_payload_md": ""}
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
        res = (
            client.table("reading_context_profiles")
            .select("person_name,title,ai_payload_json,ai_payload_md,updated_at")
            .eq("owner_id", settings.SUPABASE_ADMIN_USER_ID)
            .eq("person_name", name)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0]
        if not row:
            return {"person_name": name, "title": "", "ai_payload_json": None, "ai_payload_md": ""}
        return {
            "person_name": row.get("person_name") or name,
            "title": row.get("title") or "",
            "ai_payload_json": row.get("ai_payload_json"),
            "ai_payload_md": row.get("ai_payload_md") or "",
        }
    except Exception:
        return {"person_name": name, "title": "", "ai_payload_json": None, "ai_payload_md": ""}


def load_person_reading_contexts(person_names: List[str]) -> List[Dict[str, Any]]:
    """指定された person_names（検索絞り込み）と同名のコンテキストだけを返す。"""
    out: List[Dict[str, Any]] = []
    for n in person_names or []:
        name = (n or "").strip()
        if not name:
            continue
        out.append(load_person_reading_context(name))
    return out
