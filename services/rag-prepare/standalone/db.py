"""Supabase client for rag-prepare only (no monorepo `shared/`)."""
from __future__ import annotations

import os

from supabase import Client, create_client


class RagServiceDB:
    """Service-role Supabase client; API compatible with prior `DatabaseClient(use_service_role=True).client` usage."""

    __slots__ = ("client",)

    def __init__(self, use_service_role: bool = True) -> None:
        _ = use_service_role  # retained for call-site compatibility
        url = (os.environ.get("SUPABASE_URL") or "").strip()
        key = (
            (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
            or (os.environ.get("SUPABASE_KEY") or "").strip()
        )
        if not url or not key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) are required"
            )
        self.client: Client = create_client(url, key)
