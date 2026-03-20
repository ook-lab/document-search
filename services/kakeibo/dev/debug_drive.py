import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from drive_client import DriveClient
from config import MONEYFORWARD_FOLDER_ID

def check_folder():
    d = DriveClient()
    print(f"Target Folder ID: {MONEYFORWARD_FOLDER_ID}")
    
    # リスト表示
    files = d.list_files_in_folder(MONEYFORWARD_FOLDER_ID)
    print(f"Files in folder: {len(files)}")
    for f in files:
        print(f" - {f['name']} ({f['id']})")

    # CSV全検索 (ゴミ箱以外)
    print("
Searching for all CSVs in Drive:")
    query = "mimeType = 'text/csv' and trashed = false"
    results = d.service.files().list(q=query, fields="files(id, name, parents)").execute().get('files', [])
    for f in results:
        print(f" - {f['name']} ({f['id']}) Parent: {f.get('parents')}")

if __name__ == "__main__":
    check_folder()
