import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from drive_client import DriveClient

load_dotenv()

def deep_search():
    drive = DriveClient()
    # List all files with mimeType text/csv or ending in .csv
    query = "name contains '.csv' and trashed = false"
    try:
        results = drive.service.files().list(
            q=query,
            fields="files(id, name, parents)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        files = results.get('files', [])
        if not files:
            print("No CSV files found in entire accessible Drive.")
        for f in files:
            print(f"File: {f['name']} (ID: {f['id']}), Parents: {f.get('parents')}")
    except Exception as e:
        print(f"Search error: {e}")

if __name__ == "__main__":
    deep_search()
