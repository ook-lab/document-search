"""
Supabaseのprocessing_lockテーブルを自動修正するスクリプト
"""
import sys
from pathlib import Path

# パス設定
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from shared.common.database.client import DatabaseClient

# Supabase接続（Service Role Keyを使用）
db_client = DatabaseClient(use_service_role=True)
client = db_client.client

print("=== processing_lockテーブル修正開始 ===\n")

# 1. 現在のテーブル構造を確認
print("1. 現在のテーブル状態を確認中...")
try:
    result = client.table('processing_lock').select('*').execute()
    print(f"   既存レコード数: {len(result.data)}")
    if result.data:
        print(f"   既存カラム: {list(result.data[0].keys())}")
except Exception as e:
    print(f"   エラー: {e}")

# 2. カラム追加（RPC経由でSQLを実行）
print("\n2. 必要なカラムを追加中...")
sql = """
ALTER TABLE processing_lock
ADD COLUMN IF NOT EXISTS current_index INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS total_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS current_file TEXT DEFAULT '',
ADD COLUMN IF NOT EXISTS success_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS error_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS logs JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS cpu_percent REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS memory_percent REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS memory_used_gb REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS memory_total_gb REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS throttle_delay REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS adjustment_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS max_parallel INTEGER DEFAULT 3,
ADD COLUMN IF NOT EXISTS current_workers INTEGER DEFAULT 0;
"""

try:
    client.rpc('exec_sql', {'sql': sql}).execute()
    print("   ✓ カラム追加完了")
except Exception as e:
    # RPCが使えない場合、postgrestでDDLは実行できないので、手動実行が必要
    print(f"   ⚠ RPCでカラム追加できませんでした: {e}")
    print("   → 以下のSQLをSupabase SQL Editorで手動実行してください：")
    print(sql)

# 3. id=1のレコードを確認・作成
print("\n3. 初期レコード（id=1）を確認中...")
try:
    result = client.table('processing_lock').select('*').eq('id', 1).execute()
    if not result.data:
        print("   初期レコードが存在しません。作成します...")
        client.table('processing_lock').insert({
            'id': 1,
            'is_processing': False,
            'max_parallel': 3,
            'current_workers': 0,
            'current_index': 0,
            'total_count': 0,
            'current_file': '',
            'success_count': 0,
            'error_count': 0,
            'logs': [],
            'cpu_percent': 0.0,
            'memory_percent': 0.0,
            'memory_used_gb': 0.0,
            'memory_total_gb': 0.0,
            'throttle_delay': 0.0,
            'adjustment_count': 0
        }).execute()
        print("   ✓ 初期レコード作成完了")
    else:
        print("   ✓ 初期レコードは既に存在します")
        print(f"   内容: {result.data[0]}")
except Exception as e:
    print(f"   エラー: {e}")

print("\n=== 完了 ===")
print("ブラウザでCloud Runのページを開いて、処理を開始してください。")
print("リアルタイム表示が動作するはずです。")
