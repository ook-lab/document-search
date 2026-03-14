from dotenv import load_dotenv
from drive_client import DriveClient

load_dotenv()

def find_mf_csvs():
    drive = DriveClient()
    files = drive.search_files("name contains '.csv'")
    for f in files:
        print(f"File: {f.get('name')} (ID: {f.get('id')}), Parent: {f.get('parents')}")

if __name__ == "__main__":
    find_mf_csvs()
