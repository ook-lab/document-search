import os
from pathlib import Path
from dotenv import load_dotenv

# services/kakeibo_view/ → プロジェクトルートの .env を探す
_here = Path(__file__).parent
load_dotenv(_here / '../../.env')
load_dotenv()  # フォールバック

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
