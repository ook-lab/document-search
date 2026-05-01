"""Supabase client for daily-report only (no monorepo shared/)."""
from __future__ import annotations

import os

from supabase import Client, create_client


class SupabaseService:
    """Connect with service_role; use `.client` for PostgREST."""

    def __init__(self) -> None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        self.client: Client = create_client(url, key)