"""
設定管理
環境変数から設定を読み込む
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# .env ファイルを読み込む（システム環境変数よりも優先）
load_dotenv(override=True)


class Settings:
    """アプリケーション設定"""

    # OpenAI API Key
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Google AI API Key
    GOOGLE_AI_API_KEY: str = os.getenv("GOOGLE_AI_API_KEY", "")

    # Anthropic API Key
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    
    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    
    # Google Drive
    PERSONAL_FOLDER_ID: str = os.getenv("PERSONAL_FOLDER_ID", "")
    FAMILY_FOLDER_ID: str = os.getenv("FAMILY_FOLDER_ID", "")
    WORK_FOLDER_ID: str = os.getenv("WORK_FOLDER_ID", "")

    # Google Drive InBox監視システム用
    INBOX_FOLDER_ID: str = os.getenv("INBOX_FOLDER_ID", "")
    ARCHIVE_FOLDER_ID: str = os.getenv("ARCHIVE_FOLDER_ID", "")
    
    # プロジェクトルート
    PROJECT_ROOT: Path = Path(__file__).parent.parent
    
    # データディレクトリ
    DATA_DIR: Path = PROJECT_ROOT / "data"
    TEMP_DIR: Path = DATA_DIR / "temp"
    SCHEMAS_DIR: Path = PROJECT_ROOT / "config" / "schemas"
    
    def __init__(self):
        """初期化時にディレクトリを作成"""
        self.DATA_DIR.mkdir(exist_ok=True)
        self.TEMP_DIR.mkdir(exist_ok=True)


# シングルトンインスタンス
settings = Settings()
