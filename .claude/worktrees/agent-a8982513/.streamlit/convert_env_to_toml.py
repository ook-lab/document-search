#!/usr/bin/env python3
"""
.env ファイルを Streamlit Cloud の secrets.toml 形式に変換するヘルパースクリプト
"""
import os
from pathlib import Path


def convert_env_to_toml():
    """
    .env ファイルを読み込み、TOML形式の文字列を生成
    """
    env_file = Path(__file__).parent.parent / '.env'

    if not env_file.exists():
        print(f"❌ .env ファイルが見つかりません: {env_file}")
        return

    print("📋 Streamlit Cloud Secrets 設定用のTOML形式:")
    print("=" * 60)
    print()

    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()

            # コメント行や空行をスキップ
            if not line or line.startswith('#'):
                continue

            # KEY=VALUE の形式をパース
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()

                # 引用符を除去
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]

                # TOML形式で出力
                print(f'{key} = "{value}"')

    print()
    print("=" * 60)
    print("✅ 上記の内容を Streamlit Cloud の Secrets タブに貼り付けてください")
    print()
    print("手順:")
    print("1. https://share.streamlit.io/ にアクセス")
    print("2. アプリを選択 → Settings → Secrets")
    print("3. 上記の内容をコピー&ペースト")
    print("4. Save をクリック")


if __name__ == "__main__":
    convert_env_to_toml()
