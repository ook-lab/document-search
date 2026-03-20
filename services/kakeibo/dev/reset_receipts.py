import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""
レシート関連データを全削除してリセット
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

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

# (テーブル名, PKカラム名)
tables = [
    ("Rawdata_RECEIPT_items",     "id"),
    ("Aggregate_receipt_summary", "receipt_id"),
    ("Kakeibo_Receipt_Links",     "id"),
    ("Rawdata_RECEIPT_shops",     "id"),
    ("99_lg_image_proc_log",      "id"),
]

for t, pk in tables:
    count = len(db.table(t).select(pk).execute().data)
    print(f"  {t}: {count}件")

confirm = input("
上記を全件削除します。よいですか？ [yes/no]: ")
if confirm.strip().lower() != "yes":
    print("キャンセルしました。")
    sys.exit(0)

for t, pk in tables:
    db.table(t).delete().neq(pk, "00000000-0000-0000-0000-000000000000").execute()
    print(f"  削除完了: {t}")

print("
リセット完了。Driveのファイルをinboxに戻してから再取込してください。")
