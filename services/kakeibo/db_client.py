"""
Kakeibo DB クライアント
Supabase への直接接続（shared.common 不要）
"""
import os
from supabase import create_client, Client


_db_service_client = None
_db_anon_client = None

def get_db(force_new: bool = False) -> Client:
    """Service Role キーで Supabase クライアントを返す（app.py ルート用）"""
    global _db_service_client
    if _db_service_client is None or force_new:
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
        _db_service_client = create_client(url, key)
    return _db_service_client


def get_db_anon() -> Client:
    """Anon キーで Supabase クライアントを返す（TransactionProcessor 用）"""
    global _db_anon_client
    if _db_anon_client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _db_anon_client = create_client(url, key)
    return _db_anon_client
