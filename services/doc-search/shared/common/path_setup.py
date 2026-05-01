"""
パス設定の一元管理モジュール
すべてのスクリプトはこのモジュールをインポートするだけでパスが通る
"""
import os
import sys
from pathlib import Path

def get_project_root() -> Path:
    """プロジェクトルートを取得"""
    # 環境変数が設定されていればそれを使用
    if os.getenv('PROJECT_ROOT'):
        return Path(os.getenv('PROJECT_ROOT'))

    # このファイルから辿ってルートを特定
    # shared/common/path_setup.py -> shared/common -> shared -> project_root
    return Path(__file__).resolve().parent.parent.parent

def setup_paths():
    """Pythonパスを設定（一度だけ実行される）"""
    project_root = get_project_root()

    paths_to_add = [
        str(project_root),
        str(project_root / 'services'),
        str(project_root / 'services' / 'data-ingestion'),
    ]

    for path in paths_to_add:
        if path not in sys.path:
            sys.path.insert(0, path)

    # 環境変数も設定（サブプロセス用）
    os.environ['PROJECT_ROOT'] = str(project_root)

    return project_root

# インポート時に自動実行
PROJECT_ROOT = setup_paths()
