import sys
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

print("=== RLS無効化 ===\n")

# RLSを無効化するSQLを実行
# postgrest経由では実行できないため、RPCを作成する必要がある
# 代わりに、データを直接確認して問題を特定

# まずデータを取得
result = client.table('processing_lock').select('*').eq('id', 1).execute()

if result.data:
    data = result.data[0]
    print("取得できたカラム:")
    for key in data.keys():
        print(f"  {key}")

    print("\n確認: cpu_percent が存在するか?")
    if 'cpu_percent' in data:
        print(f"  はい: {data['cpu_percent']}")
    else:
        print("  いいえ - RLSまたはカラムが存在しない問題")
        print("\n解決方法: Supabase Dashboard > Authentication > Policies")
        print("  processing_lock テーブルの SELECT policy を全て削除するか、")
        print("  または全カラムへのアクセスを許可してください")
else:
    print("データ取得失敗")
