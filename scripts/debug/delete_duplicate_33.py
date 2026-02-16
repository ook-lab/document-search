"""学年通信 (33).pdf の重複レコード（新しい方）を削除"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)

# 削除対象: created_at が新しい方（テスト用）
# id: 01fea093-c4e2-440a-bf4c-e40ccc99d041
# created_at: 2026-01-26T01:48:47.398079+00:00

DELETE_ID = '01fea093-c4e2-440a-bf4c-e40ccc99d041'

print('=' * 80)
print('学年通信 (33).pdf の重複レコード削除')
print('=' * 80)
print(f'\n削除対象ID: {DELETE_ID}')
print('created_at: 2026-01-26T01:48:47 (新しい方 = テスト用)')

# 確認
result = db.supabase.table('Rawdata_FILE_AND_MAIL').select(
    'id, file_name, created_at, processing_status'
).eq('id', DELETE_ID).execute()

if not result.data:
    print('\nエラー: レコードが見つかりません')
    sys.exit(1)

row = result.data[0]
print(f'\n削除前の確認:')
print(f'  id: {row["id"]}')
print(f'  file_name: {row["file_name"]}')
print(f'  created_at: {row["created_at"]}')
print(f'  status: {row["processing_status"]}')

# 削除実行
print(f'\n削除実行中...')
delete_result = db.supabase.table('Rawdata_FILE_AND_MAIL').delete().eq('id', DELETE_ID).execute()

print(f'削除完了: {len(delete_result.data)}件')

# 削除後の確認
remaining = db.supabase.table('Rawdata_FILE_AND_MAIL').select('id, created_at').eq('file_name', '学年通信 (33).pdf').execute()
print(f'\n削除後の残りレコード: {len(remaining.data)}件')
for r in remaining.data:
    print(f'  id: {r["id"]}, created_at: {r["created_at"]}')

print('\n' + '=' * 80)
print('削除完了')
print('=' * 80)
