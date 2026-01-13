import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# パス設定
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from shared.common.database.client import DatabaseClient

# Supabase接続（Service Role Keyを使用）
db_client = DatabaseClient(use_service_role=True)
client = db_client.client

print("=== processing_lock 更新監視 ===")
print("2秒ごとにチェックします...\n")

last_updated = None

for i in range(10):
    result = client.table('processing_lock').select('updated_at,cpu_percent,memory_percent,current_index,total_count,is_processing').eq('id', 1).execute()

    if result.data:
        data = result.data[0]
        now = datetime.now().strftime('%H:%M:%S')

        if last_updated != data['updated_at']:
            print(f"[{now}] 🔄 更新されました！")
            print(f"  updated_at: {data['updated_at']}")
            print(f"  cpu_percent: {data['cpu_percent']}")
            print(f"  memory_percent: {data['memory_percent']}")
            print(f"  進捗: {data['current_index']}/{data['total_count']}")
            print(f"  is_processing: {data['is_processing']}")
            print()
            last_updated = data['updated_at']
        else:
            print(f"[{now}] ⏸️  変化なし (updated_at: {data['updated_at']})")

    time.sleep(2)

print("\n監視終了")
