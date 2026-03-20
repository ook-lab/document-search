import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
from dotenv import load_dotenv
from drive_client import DriveClient
from config import MONEYFORWARD_FOLDER_ID, MONEYFORWARD_PROCESSED_ID

load_dotenv()

def list_all_files():
    drive = DriveClient()
    print(f"Listing folder: {MONEYFORWARD_FOLDER_ID} (Source)")
    files = drive.list_files_in_folder(MONEYFORWARD_FOLDER_ID)
    for f in files:
        print(f" - {f.get('name')} ({f.get('id')})")
        
    print(f"Listing folder: {MONEYFORWARD_PROCESSED_ID} (Processed)")
    files = drive.list_files_in_folder(MONEYFORWARD_PROCESSED_ID)
    for f in files:
        print(f" - {f.get('name')} ({f.get('id')})")

if __name__ == "__main__":
    list_all_files()
