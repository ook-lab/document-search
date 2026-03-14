#!/usr/bin/env python
"""パスを正しく設定してからサーバーを起動"""
import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).resolve().parent.parent.parent
services_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(services_root))

print(f"project_root: {project_root}")
print(f"sys.path[:3]: {sys.path[:3]}")

# appをインポートして起動
from app import app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
