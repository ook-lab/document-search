"""学年通信(33).pdf の重複レコード確認"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)

# 学年通信(33).pdf のレコードを確認
result = db.supabase.table('Rawdata_FILE_AND_MAIL').select(
    'id, file_name, created_at, processing_status, metadata'
).eq('file_name', '学年通信(33).pdf').order('created_at').execute()

print('=' * 80)
print('学年通信(33).pdf のレコード:')
print('=' * 80)

for idx, row in enumerate(result.data, 1):
    print(f"\nレコード #{idx}:")
    print(f"  id: {row['id']}")
    print(f"  file_name: {row['file_name']}")
    print(f"  created_at: {row['created_at']}")
    print(f"  status: {row['processing_status']}")

    # metadata から g11_output, g12_output を確認
    metadata = row.get('metadata')
    if metadata and isinstance(metadata, dict):
        g11 = metadata.get('g11_output', 'N/A')
        g12 = metadata.get('g12_output', 'N/A')
        if isinstance(g11, list):
            g11 = len(g11)
        if isinstance(g12, list):
            g12 = len(g12)
        print(f"  g11_output: {g11}")
        print(f"  g12_output: {g12}")
    else:
        print(f"  metadata: {metadata}")

print('\n' + '=' * 80)
print(f'合計: {len(result.data)}件')
print('=' * 80)
