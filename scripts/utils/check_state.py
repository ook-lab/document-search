"""Check current processing state in Supabase"""
from shared.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)

# processing_workers テーブルを確認
workers = db.client.table('processing_workers').select('*').execute()
print('=== processing_workers ===')
for w in workers.data:
    print(f'  - {w}')
print(f'Total: {len(workers.data)}')

# processing_lock テーブルを確認
lock = db.client.table('processing_lock').select('*').eq('id', 1).execute()
print()
print('=== processing_lock ===')
print(lock.data)

# processing状態のドキュメント数を確認
docs = db.client.table('Rawdata_FILE_AND_MAIL').select('id', count='exact').eq('processing_status', 'processing').execute()
print()
print(f'=== processing状態のドキュメント数: {docs.count} ===')
