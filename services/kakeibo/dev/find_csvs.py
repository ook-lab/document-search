import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
from dotenv import load_dotenv
from drive_client import DriveClient

load_dotenv()

def find_csvs():
    drive = DriveClient()
    for key, val in os.environ.items():
        if '_FOLDER_ID' in key and val:
            try:
                files = drive.list_files_in_folder(val)
                csvs = [f for f in files if f.get('name', '').endswith('.csv')]
                if csvs:
                    print(f"Folder {key} ({val}) has CSVs:")
                    for c in csvs:
                        print(f"  - {c['name']} (ID: {c['id']})")
            except Exception as e:
                print(f"Error checking {key}: {e}")

if __name__ == "__main__":
    find_csvs()
