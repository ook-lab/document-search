import os
import io
import csv
from dotenv import load_dotenv
from drive_client import DriveClient
from config import MONEYFORWARD_FOLDER_ID, MONEYFORWARD_PROCESSED_ID

load_dotenv()

def check_csv_headers():
    drive = DriveClient()
    files = drive.list_files_in_folder(MONEYFORWARD_FOLDER_ID)
    if not files:
        print("Checking processed folder...")
        files = drive.list_files_in_folder(MONEYFORWARD_PROCESSED_ID)
        
    csv_files = [f for f in files if f.get("name", "").endswith(".csv")]
    
    if not csv_files:
        print("No CSV files found in either folder.")
        return
    
    file_id = csv_files[0]["id"]
    file_name = csv_files[0]["name"]
    print(f"Checking headers for: {file_name}")
    
    raw_bytes = drive.get_file_bytes(file_id)
    text = raw_bytes.decode("cp932")
    reader = csv.reader(io.StringIO(text))
    headers = next(reader)
    print("Headers Found:", headers)

if __name__ == "__main__":
    check_csv_headers()
