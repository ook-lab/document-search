"""Supabase for netsuper-search (no monorepo dms/)."""
from __future__ import annotations

import os

from supabase import Client, create_client


class SupabaseService:
    """Supabase client; use ``.client`` for PostgREST."""

    def __init__(self, use_service_role: bool = False) -> None:
        url = os.environ.get("SUPABASE_URL")
        if not url:
            raise ValueError("SUPABASE_URL is required")
        if use_service_role:
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            if not key:
                raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required when use_service_role=True")
        else:
            key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
            if not key:
                raise ValueError("SUPABASE_KEY (or SUPABASE_ANON_KEY) is required for anon access")
        self.client: Client = create_client(url, key)
