"""
設定管理
環境変数から設定を読み込む
"""
import os
from pathlib import Path
from dotenv import load_dotenv

def _find_project_root() -> Path:
    """プロジェクトルートを特定"""
    # 1. 環境変数で明示的に指定されている場合
    if os.getenv('PROJECT_ROOT'):
        return Path(os.getenv('PROJECT_ROOT'))

    # 2. このファイルから辿る (shared/common/config/settings.py)
    return Path(__file__).resolve().parent.parent.parent.parent

def _load_env_file():
    """環境変数ファイルを読み込む"""
    # 1. 環境変数で.envファイルパスが指定されている場合
    if os.getenv('ENV_FILE_PATH'):
        env_path = Path(os.getenv('ENV_FILE_PATH'))
        if env_path.exists():
            load_dotenv(env_path, override=False)
            return

    # 2. プロジェクトルートの.envを探す
    project_root = _find_project_root()
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
        return

    # 3. フォールバック: dotenvの自動探索1
    load_dotenv(override=False)

# 初期化時に環境変数を読み込む
_project_root = _find_project_root()
_load_env_file()


class Settings:
    """アプリケーション設定"""

    # ========== アーキテクチャ原則 ==========
    # Web (Cloud Run / localhost) は enqueue・閲覧・運用操作のみ
    # 処理実行は Worker (CLI) のみ
    # この原則は設定で変更不可（構造的に強制）

    # OpenAI API Key
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Google AI API Key
    GOOGLE_AI_API_KEY: str = os.getenv("GOOGLE_AI_API_KEY", "")

    # Anthropic API Key
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    
    # Supabase (Settingsクラスの中)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    # SUPABASE_ANON_KEY と SUPABASE_KEY の両方に対応させる
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", os.getenv("SUPABASE_KEY", ""))
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    
    # Google Drive
    PERSONAL_FOLDER_ID: str = os.getenv("PERSONAL_FOLDER_ID", "")
    FAMILY_FOLDER_ID: str = os.getenv("FAMILY_FOLDER_ID", "")
    WORK_FOLDER_ID: str = os.getenv("WORK_FOLDER_ID", "")

    # Google Drive InBox監視システム用
    INBOX_FOLDER_ID: str = os.getenv("INBOX_FOLDER_ID", "")
    ARCHIVE_FOLDER_ID: str = os.getenv("ARCHIVE_FOLDER_ID", "")
    
    # プロジェクトルート
    PROJECT_ROOT: Path = _project_root

    # データディレクトリ
    DATA_DIR: Path = Path("/tmp/data")
    TEMP_DIR: Path = Path("/tmp/temp")
    SCHEMAS_DIR: Path = PROJECT_ROOT / "frontend" / "schemas"
    
    def __init__(self):
        """初期化時にディレクトリを作成"""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.TEMP_DIR.mkdir(exist_ok=True)


# シングルトンインスタンス
settings = Settings()
