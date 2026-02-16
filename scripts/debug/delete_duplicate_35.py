"""学年通信 (35).pdf の重複レコード（新しい2件）を削除"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)

# 削除対象: created_at が新しい2件
DELETE_IDS = [
    '15b8f277-f7cd-43c4-873d-7baa3aa96a99',  # 2026-01-26 01:48
    '338fd64d-ff2f-4dec-b7da-93eae786ceda',  # 2026-01-28 06:48
]

# 残すレコード
KEEP_ID = '10faedb1-f923-435e-a202-bf5aaf8d9e04'  # 2026-01-25 15:04 (最古)

print('=' * 80)
print('学年通信 (35).pdf の重複レコード削除')
print('=' * 80)
print(f'\n残すレコード: {KEEP_ID}')
print('  created_at: 2026-01-25 15:04 (最古)')

print(f'\n削除対象: {len(DELETE_IDS)}件')
for idx, delete_id in enumerate(DELETE_IDS, 1):
    print(f'  #{idx}: {delete_id}')

# 削除実行
deleted_count = 0
for delete_id in DELETE_IDS:
    print(f'\n削除中: {delete_id}...')
    delete_result = db.supabase.table('Rawdata_FILE_AND_MAIL').delete().eq('id', delete_id).execute()
    if delete_result.data:
        deleted_count += len(delete_result.data)
        print(f'  削除完了')
    else:
        print(f'  削除失敗またはレコードなし')

print(f'\n総削除件数: {deleted_count}件')

# 削除後の確認
remaining = db.supabase.table('Rawdata_FILE_AND_MAIL').select('id, created_at, processing_status').eq('file_name', '学年通信 (35).pdf').execute()
print(f'\n削除後の残りレコード: {len(remaining.data)}件')
for r in remaining.data:
    print(f'  id: {r["id"]}')
    print(f'  created_at: {r["created_at"]}')
    print(f'  status: {r["processing_status"]}')

print('\n' + '=' * 80)
print('削除完了')
print('=' * 80)
