#!/usr/bin/env python3
"""
DB参照ズレ解消スクリプト
- 10_ix_search_index のカラム構造を確認
- search_index 互換VIEW と match_documents 関数のSQLを生成
"""
import os
import sys
from pathlib import Path

# プロジェクトルートを追加
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from supabase import create_client

# 接続
url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
client = create_client(url, key)

print("=" * 60)
print("Step 1: 10_ix_search_index のカラム構造を確認")
print("=" * 60)

# テーブルから1行取得してカラム名を確認
try:
    response = client.table('10_ix_search_index').select('*').limit(1).execute()
    if response.data:
        columns = list(response.data[0].keys())
        print(f"カラム一覧: {columns}")
        print(f"\nサンプルデータ:")
        for k, v in response.data[0].items():
            val_str = str(v)[:100] + '...' if len(str(v)) > 100 else str(v)
            print(f"  {k}: {val_str}")
    else:
        print("テーブルは空です")
except Exception as e:
    print(f"エラー: {e}")

print("\n" + "=" * 60)
print("Step 2: Rawdata_FILE_AND_MAIL のカラム構造を確認")
print("=" * 60)

try:
    response = client.table('Rawdata_FILE_AND_MAIL').select('*').limit(1).execute()
    if response.data:
        columns = list(response.data[0].keys())
        print(f"カラム一覧: {columns}")
    else:
        print("テーブルは空です")
except Exception as e:
    print(f"エラー: {e}")

print("\n" + "=" * 60)
print("Step 3: 既存のsearch_indexビューまたはテーブルを確認")
print("=" * 60)

try:
    response = client.table('search_index').select('*').limit(1).execute()
    if response.data:
        columns = list(response.data[0].keys())
        print(f"search_index が存在します。カラム: {columns}")
    else:
        print("search_index は空または存在しません")
except Exception as e:
    print(f"search_index へのアクセス結果: {e}")
