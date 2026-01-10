"""
既存の商品データを新しいdoc_type/source_typeに更新
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Windows環境でのUnicode出力設定
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# プロジェクトルートをパスに追加
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

load_dotenv(root_dir / ".env")

from shared.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)

print("="*80)
print("商品データ更新スクリプト")
print("="*80)

# 1. ネットスーパー商品を更新
print("\n[1] ネットスーパー商品を更新...")
net_supermarkets = ["楽天西友ネットスーパー", "東急ストア ネットスーパー", "ダイエーネットスーパー"]

for org in net_supermarkets:
    result = db.client.table('Rawdata_NETSUPER_items').update({
        "source_type": "online_supermarket",
        "doc_type": "online_grocery_item"
    }).eq('organization', org).execute()

    print(f"  ✅ {org}: 更新完了")

# 2. レシート商品を更新
print("\n[2] レシート由来商品を更新...")

# まずレシート由来の商品を確認
receipt_products = db.client.table('Rawdata_NETSUPER_items').select(
    'id, metadata'
).eq('organization', 'レシート').execute()

print(f"  レシート由来商品: {len(receipt_products.data)}件")

# 各商品のreceipt_idから店舗名を取得して更新
updated_count = 0
for product in receipt_products.data:
    metadata = product.get('metadata', {})
    receipt_id = metadata.get('receipt_id')

    if receipt_id:
        # receipt_idから店舗名を取得
        receipt = db.client.table('Rawdata_RECEIPT_shops').select('shop_name').eq('id', receipt_id).execute()

        if receipt.data and receipt.data[0].get('shop_name'):
            shop_name = receipt.data[0]['shop_name']

            # 商品を更新
            db.client.table('Rawdata_NETSUPER_items').update({
                "organization": shop_name,
                "source_type": "physical_store",
                "doc_type": "Receipt",
                "workspace": "shopping"
            }).eq('id', product['id']).execute()

            updated_count += 1

            if updated_count % 10 == 0:
                print(f"    進捗: {updated_count}/{len(receipt_products.data)}")

print(f"  ✅ {updated_count}件のレシート商品を更新しました")

# 3. 確認
print("\n[3] 更新結果を確認...")
result = db.client.table('Rawdata_NETSUPER_items').select('organization, source_type, doc_type').execute()

# 集計
stats = {}
for row in result.data:
    key = (row.get('source_type'), row.get('doc_type'))
    stats[key] = stats.get(key, 0) + 1

print("\n📊 更新後の統計:")
for (source_type, doc_type), count in stats.items():
    print(f"  {source_type} / {doc_type}: {count}件")

print("\n" + "="*80)
print("✅ 更新完了")
print("="*80)
