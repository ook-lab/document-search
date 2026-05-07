"""設定: Secret Manager を優先し、環境変数で上書き可能（ローカル用）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env")

GCP_PROJECT_ID = (os.getenv("GCP_PROJECT_ID") or "").strip()


def _sm_read(secret_id: str) -> Optional[str]:
    """Secret Manager の最新バージョンを UTF-8 で返す。失敗時は None。"""
    if not GCP_PROJECT_ID or not secret_id:
        return None
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        return resp.payload.data.decode("utf-8")
    except Exception:
        return None


def _bundle_from_sm() -> Dict[str, Any]:
    """1 本の JSON シークレットからまとめて読む（任意）。"""
    bundle_id = (os.getenv("READING_CONTEXT_SECRET_BUNDLE") or "reading-context-editor-config").strip()
    raw = _sm_read(bundle_id)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def _resolve() -> tuple[str, str, str]:
    """
    値の優先順位（各キーごと）:
      1. 環境変数 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY|SUPABASE_KEY / SUPABASE_ADMIN_USER_ID（上書き・ローカル）
      2. バンドル JSON（READING_CONTEXT_SECRET_BUNDLE、既定 reading-context-editor-config）
      3. 個別シークレット（READING_CONTEXT_SM_*_SECRET でシークレット ID を変更可）
    """
    b = _bundle_from_sm() if GCP_PROJECT_ID else {}

    sm_url = (b.get("SUPABASE_URL") or "").strip()
    sm_key = (b.get("SUPABASE_SERVICE_ROLE_KEY") or b.get("SUPABASE_KEY") or "").strip()
    sm_owner = (b.get("SUPABASE_ADMIN_USER_ID") or "").strip()

    if GCP_PROJECT_ID:
        if not sm_url:
            sid = (os.getenv("READING_CONTEXT_SM_URL_SECRET") or "reading-context-editor-supabase-url").strip()
            sm_url = (_sm_read(sid) or "").strip()
        if not sm_key:
            sid = (
                os.getenv("READING_CONTEXT_SM_SERVICE_ROLE_SECRET")
                or "reading-context-editor-supabase-service-role-key"
            ).strip()
            sm_key = (_sm_read(sid) or "").strip()
        if not sm_owner:
            sid = (os.getenv("READING_CONTEXT_SM_OWNER_SECRET") or "SUPABASE_ADMIN_USER_ID").strip()
            sm_owner = (_sm_read(sid) or "").strip()

    url = (os.getenv("SUPABASE_URL") or "").strip() or sm_url
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or "").strip() or sm_key
    owner = (os.getenv("SUPABASE_ADMIN_USER_ID") or "").strip() or sm_owner
    return url, key, owner


SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ADMIN_USER_ID = _resolve()
