#!/usr/bin/env python3
"""停滞検知実装のための事前確認"""
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
print("事前確認: 運用トレース列")
print("=" * 70)

# 1. トレース関連カラム確認
print("\n[1] Rawdata_FILE_AND_MAIL のトレース関連カラム:")
try:
    response = client.table('Rawdata_FILE_AND_MAIL').select('*').limit(1).execute()
    if response.data:
        row = response.data[0]
        trace_cols = [
            'processing_started_at', 'processing_heartbeat_at',
            'processing_worker_id', 'updated_at', 'created_at'
        ]
        for col in trace_cols:
            exists = col in row
            val = row.get(col, 'N/A') if exists else '(存在しない)'
            print(f"  {col}: {'存在' if exists else '要追加'} -> {val}")
except Exception as e:
    print(f"  エラー: {e}")

# 2. 現在の停滞状況
print("\n[2] 停滞状況（pending/processing で古いもの）:")
try:
    # pending で古いもの
    response = client.table('Rawdata_FILE_AND_MAIL').select(
        'id, processing_status, created_at'
    ).in_('processing_status', ['pending', 'processing']).order(
        'created_at', desc=False
    ).limit(5).execute()

    if response.data:
        for row in response.data:
            print(f"  id={row['id'][:8]}..., status={row['processing_status']}, created={row['created_at']}")
    else:
        print("  停滞なし")
except Exception as e:
    print(f"  エラー: {e}")

# 3. retry_queue と Rawdata の整合性
print("\n[3] 整合性チェック:")
try:
    # retry_queue の件数
    rq = client.table('retry_queue').select('rawdata_id, status').execute()
    print(f"  retry_queue総数: {len(rq.data)}")

    # Rawdata の failed 件数
    failed = client.table('Rawdata_FILE_AND_MAIL').select('id', count='exact').eq('processing_status', 'failed').execute()
    print(f"  Rawdata failed: {failed.count}")

    # completed だけど retry_queue に残っている
    completed_ids = client.table('Rawdata_FILE_AND_MAIL').select('id').eq('processing_status', 'completed').execute()
    completed_set = {r['id'] for r in completed_ids.data}
    rq_set = {r['rawdata_id'] for r in rq.data if r['status'] in ('queued', 'leased')}
    orphan = completed_set & rq_set
    print(f"  completed但しretry_queue残存: {len(orphan)}")

except Exception as e:
    print(f"  エラー: {e}")

print("\n" + "=" * 70)
