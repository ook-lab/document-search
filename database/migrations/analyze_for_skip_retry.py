#!/usr/bin/env python3
"""
スキップallowlist + 再処理キュー実装のための事前確認
"""
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
client = create_client(url, key)

print("=" * 70)
print("事前確認: スキップallowlist + 再処理キュー")
print("=" * 70)

# 1. Rawdataの状態関連カラム確認
print("\n[1] Rawdata_FILE_AND_MAIL の既存カラム確認:")
try:
    response = client.table('Rawdata_FILE_AND_MAIL').select('*').limit(1).execute()
    if response.data:
        row = response.data[0]
        # skip関連
        skip_cols = ['skip_code', 'skip_reason', 'skipped_at']
        print("  skip関連カラム:")
        for col in skip_cols:
            exists = col in row
            print(f"    {col}: {'存在' if exists else '要追加'}")

        # retry関連
        retry_cols = ['retry_count', 'last_retry_at']
        print("  retry関連カラム:")
        for col in retry_cols:
            exists = col in row
            print(f"    {col}: {'存在' if exists else '要追加'}")

        # 既存エラー関連
        error_cols = ['processing_status', 'processing_error', 'error_message', 'failed_stage', 'failed_at']
        print("  エラー関連カラム:")
        for col in error_cols:
            exists = col in row
            val = row.get(col, 'N/A') if exists else '(存在しない)'
            print(f"    {col}: {'存在' if exists else '要追加'}")
except Exception as e:
    print(f"  エラー: {e}")

# 2. 既存トリガ確認
print("\n[2] 既存トリガ/関数の確認:")
try:
    # completion_guard関数の存在確認
    response = client.rpc('guard_completed_status', {}).execute()
except Exception as e:
    # 関数は存在するがパラメータなしで呼ぶとエラーになる
    if 'guard_completed_status' in str(e):
        print("  guard_completed_status関数: 存在（トリガ用）")
    else:
        print(f"  guard_completed_status関数: 確認エラー - {e}")

# 3. retry_queueテーブルの存在確認
print("\n[3] retry_queueテーブルの確認:")
try:
    response = client.table('retry_queue').select('*').limit(1).execute()
    print("  retry_queue: 存在")
    if response.data:
        print(f"    カラム: {list(response.data[0].keys())}")
except Exception as e:
    if 'Could not find' in str(e):
        print("  retry_queue: 存在しない（要作成）")
    else:
        print(f"  retry_queue: 確認エラー - {e}")

# 4. skipped の現在の件数
print("\n[4] processing_status='skipped' の現在件数:")
try:
    response = client.table('Rawdata_FILE_AND_MAIL').select('id', count='exact').eq('processing_status', 'skipped').execute()
    count = response.count if response.count else 0
    print(f"  skipped件数: {count}")
except Exception as e:
    print(f"  エラー: {e}")

# 5. failed の現在の件数
print("\n[5] processing_status='failed' の現在件数:")
try:
    response = client.table('Rawdata_FILE_AND_MAIL').select('id, error_message', count='exact').eq('processing_status', 'failed').execute()
    count = response.count if response.count else 0
    print(f"  failed件数: {count}")
    if response.data:
        for row in response.data[:3]:
            err = row.get('error_message', 'N/A')
            if err:
                err = err[:60] + '...' if len(str(err)) > 60 else err
            print(f"    id={row['id'][:8]}..., error={err}")
except Exception as e:
    print(f"  エラー: {e}")

print("\n" + "=" * 70)
print("事前確認完了")
print("=" * 70)
