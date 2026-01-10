from shared.common.database.client import DatabaseClient
import sys

db = DatabaseClient()
doc_id = '2a16467c-435b-44ab-80f8-d9f8c1670495'

# 現在のステータス確認
current = db.client.table('Rawdata_FILE_AND_MAIL').select('processing_status').eq('id', doc_id).execute()
if current.data:
    print(f'Current status: {current.data[0]["processing_status"]}')

# pendingに戻す
result = db.client.table('Rawdata_FILE_AND_MAIL').update({
    'processing_status': 'pending'
}).eq('id', doc_id).execute()

print(f'Reset to pending: {doc_id}')
