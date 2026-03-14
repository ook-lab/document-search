import sys
import os

# Set environment variable to make sure .env is correctly evaluated if needed
sys.path.append("c:/Users/ookub/document-management-system/services/kakeibo")

from config import INBOX_EASY_FOLDER_ID
from drive_client import DriveClient

def main():
    print(f"INBOX_EASY_FOLDER_ID: {INBOX_EASY_FOLDER_ID}")
    drive = DriveClient()
    print("DriveClient initialized.")
    try:
        files = drive.list_files_in_folder(INBOX_EASY_FOLDER_ID)
        print(f"Files found: {len(files)}")
        for f in files:
            print(f" - {f['name']}")
    except Exception as e:
        print(f"Failed to list files: {e}")

if __name__ == "__main__":
    main()
