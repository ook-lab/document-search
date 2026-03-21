"""
DBに存在しないARCHIVEファイルを特定し、INBOXに戻して再取込する
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
env_path = Path(__file__).parent.parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from db_client import get_db
from drive_client import DriveClient
from config import ARCHIVE_FOLDER_ID, INBOX_EASY_FOLDER_ID

print("=== 欠損レシート復元スクリプト ===\n")

# ── 1. DBに登録済みの drive_file_id を取得 ──────────────────────────
print("Step 1: DBの drive_file_id を取得中...")
db = get_db()

# 1000件以上に備えてページング
known_ids = set()
offset = 0
while True:
    res = db.table("Rawdata_RECEIPT_shops") \
        .select("drive_file_id") \
        .not_.is_("drive_file_id", "null") \
        .range(offset, offset + 999) \
        .execute()
    if not res.data:
        break
    for r in res.data:
        if r.get("drive_file_id"):
            known_ids.add(r["drive_file_id"])
    if len(res.data) < 1000:
        break
    offset += 1000

print(f"  DB登録済み: {len(known_ids)}件\n")

# ── 2. ARCHIVEフォルダのファイル一覧を取得 ──────────────────────────
print("Step 2: ARCHIVEフォルダのファイル一覧を取得中...")
if not ARCHIVE_FOLDER_ID:
    print("ERROR: KAKEIBO_ARCHIVE_FOLDER_ID が未設定です")
    sys.exit(1)
if not INBOX_EASY_FOLDER_ID:
    print("ERROR: KAKEIBO_INBOX_EASY_FOLDER_ID が未設定です")
    sys.exit(1)

drive = DriveClient()
archive_files = drive.list_files_in_folder(ARCHIVE_FOLDER_ID)
print(f"  ARCHIVEファイル数: {len(archive_files)}件\n")

# ── 3. DBに存在しないファイルを特定 ──────────────────────────────────
print("Step 3: DBに存在しないファイルを特定中...")
missing = [f for f in archive_files if f["id"] not in known_ids]
print(f"  DBに存在しないファイル: {len(missing)}件\n")

if not missing:
    print("欠損ファイルなし。終了します。")
    sys.exit(0)

for f in missing:
    print(f"  - {f['name']} (id={f['id']})")

print(f"\n上記 {len(missing)} 件を INBOX_EASY に移動して再取込します。")
confirm = input("実行しますか？ [yes/no]: ")
if confirm.strip().lower() != "yes":
    print("キャンセルしました。")
    sys.exit(0)

# ── 4. ARCHIVE → INBOX_EASY へ移動 ───────────────────────────────────
print("\nStep 4: ファイルをINBOX_EASYに移動中...")
moved = 0
failed = 0
for f in missing:
    try:
        drive.move_file(f["id"], INBOX_EASY_FOLDER_ID)
        print(f"  ✓ {f['name']}")
        moved += 1
    except Exception as e:
        print(f"  ✗ {f['name']}: {e}")
        failed += 1

print(f"\n移動完了: {moved}件成功 / {failed}件失敗")
print("\nStep 5: バッチ処理を起動してください。")
print("  → kakeibo管理画面の「レシート取込」ボタンを押すか、")
print("     curl -X POST http://localhost:5001/api/batch_process_receipts")
