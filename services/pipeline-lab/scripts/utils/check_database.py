import os
import sys
from dotenv import load_dotenv
from supabase import create_client

# 出力をUTF-8に設定
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
client = create_client(url, key)

print("=== processing_lock確認 ===\n")

# id=1のレコードを取得
result = client.table('processing_lock').select('*').eq('id', 1).execute()

if result.data:
    print("id=1のレコード:")
    record = result.data[0]
    for key, value in record.items():
        print(f"  {key}: {value}")
else:
    print("id=1のレコードが存在しません！")
    print("\n作成します...")
    client.table('processing_lock').insert({
        'id': 1,
        'is_processing': False,
        'max_parallel': 1,
        'current_workers': 0
    }).execute()
    print("作成完了")
