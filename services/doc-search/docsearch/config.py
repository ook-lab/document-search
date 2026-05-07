"""doc-search の環境設定（サービスディレクトリ直下の .env のみ参照）。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def service_root() -> Path:
    """services/doc-search ディレクトリ。"""
    return Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    if os.getenv("ENV_FILE_PATH"):
        p = Path(os.getenv("ENV_FILE_PATH", ""))
        if p.exists():
            load_dotenv(p, override=False)
            return
    root = service_root()
    for candidate in (root, root.parent.parent):
        env_file = candidate / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)
            return
    load_dotenv(override=False)


_load_env_file()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GOOGLE_AI_API_KEY: str = os.getenv("GOOGLE_AI_API_KEY", "")
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    SUPABASE_ADMIN_USER_ID: str = os.getenv("SUPABASE_ADMIN_USER_ID", "")
    DATE_RANGE_THRESHOLD_DELTA: float = float(os.getenv("DATE_RANGE_THRESHOLD_DELTA", "0.0"))
    REL_SIM_MIX_EPS: float = float(os.getenv("REL_SIM_MIX_EPS", "0.0"))
    # unified_search_v2 の LIMIT。画面で返す件数とは別（期間内で並べ替えたあと何件まで返すか）。
    UNIFIED_SEARCH_RPC_MATCH_COUNT: int = int(os.getenv("UNIFIED_SEARCH_RPC_MATCH_COUNT", "80"))


settings = Settings()
