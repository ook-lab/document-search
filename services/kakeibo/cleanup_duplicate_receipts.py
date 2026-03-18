"""
重複shopレコードのクリーンアップ
- drive_file_id が同じレコードが複数ある場合、items がある1件を残して削除
- items がどれにもない場合は最新1件を残して削除
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# .env 読み込み
from pathlib import Path
env_path = Path(__file__).parent.parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from db_client import get_db

db = get_db()

# 全shopレコード取得
print("shopレコードを取得中...")
shops_res = db.table("Rawdata_RECEIPT_shops") \
    .select("id, drive_file_id, transaction_date, shop_name, created_at") \
    .order("created_at", desc=False) \
    .execute()
shops = shops_res.data
print(f"  総件数: {len(shops)}件")

# drive_file_id でグループ化
from collections import defaultdict
groups = defaultdict(list)
for s in shops:
    key = s.get("drive_file_id") or s["id"]  # drive_file_id がない場合は単独扱い
    groups[key].append(s)

duplicates = {k: v for k, v in groups.items() if len(v) > 1}
print(f"  重複グループ数: {len(duplicates)}グループ")

# 各グループで items 件数を確認
delete_ids = []

for drive_file_id, group in duplicates.items():
    shop_ids = [s["id"] for s in group]

    # 各shopのitems件数を取得
    items_counts = {}
    for shop_id in shop_ids:
        res = db.table("Rawdata_RECEIPT_items") \
            .select("id") \
            .eq("receipt_id", shop_id) \
            .execute()
        items_counts[shop_id] = len(res.data)

    # items があるものを優先して1件残す
    has_items = [sid for sid, cnt in items_counts.items() if cnt > 0]

    if has_items:
        keep_id = has_items[0]  # items があるものを残す（複数あれば最初の1件）
    else:
        # items が全部0なら最新（created_at が最後）を残す
        keep_id = group[-1]["id"]

    to_delete = [s for s in group if s["id"] != keep_id]
    for s in to_delete:
        delete_ids.append(s["id"])
        print(f"  削除対象: {s['id']} | {s.get('shop_name')} | {s.get('transaction_date')} | items={items_counts[s['id']]}")

print(f"\n削除対象: {len(delete_ids)}件")

if not delete_ids:
    print("削除対象なし。終了。")
    sys.exit(0)

confirm = input(f"\n{len(delete_ids)}件のshopレコード（および紐付くitems）を削除します。よいですか？ [yes/no]: ")
if confirm.strip().lower() != "yes":
    print("キャンセルしました。")
    sys.exit(0)

# 削除実行
print("\n削除中...")
for shop_id in delete_ids:
    # items を先に削除
    db.table("Rawdata_RECEIPT_items").delete().eq("receipt_id", shop_id).execute()
    # shopレコードを削除
    db.table("Rawdata_RECEIPT_shops").delete().eq("id", shop_id).execute()
    print(f"  削除済: {shop_id}")

print(f"\n完了。{len(delete_ids)}件削除しました。")
