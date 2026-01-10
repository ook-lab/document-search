"""
重複レコードの安全なクリーンアップ
外部キー参照を更新してから削除
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
root_dir = Path.cwd()
sys.path.insert(0, str(root_dir))
from dotenv import load_dotenv
load_dotenv(root_dir / '.env')
from shared.common.database.client import DatabaseClient
from collections import defaultdict

db = DatabaseClient(use_service_role=True)

print("="*80)
print("重複レコードクリーンアップ（安全版）")
print("="*80)

# 全商品を取得
result = db.client.table('Rawdata_NETSUPER_items').select(
    'id, product_name, organization, jan_code, created_at'
).execute()

print(f"\n総レコード数: {len(result.data)}件")

# JANコードがない商品でグループ化
no_jan_products = [row for row in result.data if not row.get('jan_code')]
print(f"JANコードなし商品: {len(no_jan_products)}件")

# 商品名+組織でグループ化
groups = defaultdict(list)
for row in no_jan_products:
    key = (row.get('product_name'), row.get('organization'))
    groups[key].append(row)

# 重複を検出（2件以上のグループ）
duplicates = {k: v for k, v in groups.items() if len(v) > 1}
print(f"\n重複グループ数: {len(duplicates)}件")

# ID変換マッピングを作成（削除するID → 保持するID）
id_mapping = {}
to_delete = []

for (product_name, org), records in duplicates.items():
    # created_atで降順ソート（最新が先頭）
    sorted_records = sorted(records, key=lambda x: x['created_at'], reverse=True)

    keep_id = sorted_records[0]['id']  # 保持するID
    old_records = sorted_records[1:]   # 削除対象

    for record in old_records:
        id_mapping[record['id']] = keep_id
        to_delete.append(record['id'])

print(f"\n削除対象レコード総数: {len(to_delete)}件")
print(f"ID変換マッピング数: {len(id_mapping)}件")

# 確認
response = input("\n1. ログテーブルのproduct_id更新\n2. 重複レコード削除\nを実行しますか？ (yes/no): ")
if response.lower() == 'yes':
    print("\n【Step 1】ログテーブルの参照を更新中...")
    updated_log_count = 0

    for old_id, new_id in id_mapping.items():
        try:
            # 99_lg_gemini_classification_log のproduct_idを更新
            result = db.client.table('99_lg_gemini_classification_log').update({
                'product_id': new_id
            }).eq('product_id', old_id).execute()

            if result.data:
                updated_log_count += len(result.data)
        except Exception as e:
            print(f"⚠ ログ更新失敗 {old_id} -> {new_id}: {e}")

    print(f"✅ {updated_log_count}件のログレコードを更新しました")

    print("\n【Step 2】重複レコードを削除中...")
    deleted_count = 0
    failed_ids = []

    for record_id in to_delete:
        try:
            db.client.table('Rawdata_NETSUPER_items').delete().eq('id', record_id).execute()
            deleted_count += 1
        except Exception as e:
            print(f"❌ 削除失敗 {record_id}: {e}")
            failed_ids.append(record_id)

    print(f"\n✅ {deleted_count}件のレコードを削除しました")
    if failed_ids:
        print(f"❌ 削除失敗: {len(failed_ids)}件")

    # 削除後の状態を確認
    after_result = db.client.table('Rawdata_NETSUPER_items').select('id').execute()
    print(f"\n削除後の総レコード数: {len(after_result.data)}件")
    print(f"削減数: {len(result.data)} -> {len(after_result.data)} (-{len(result.data) - len(after_result.data)}件)")
else:
    print("\n❌ 処理をキャンセルしました")

print("\n" + "="*80)
