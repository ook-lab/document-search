import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from drive_client import DriveClient
from config import MONEYFORWARD_FOLDER_ID, MONEYFORWARD_PROCESSED_ID
import io
import csv

load_dotenv()

def check_mf_csv():
    drive = DriveClient()
    # Check source folder
    files = drive.list_files_in_folder(MONEYFORWARD_FOLDER_ID)
    if not files:
        # Check processed folder
        files = drive.list_files_in_folder(MONEYFORWARD_PROCESSED_ID)
    
    csv_files = [f for f in files if f.get("name", "").endswith(".csv")]
    if not csv_files:
        print("No CSV files found.")
        return
        
    f = csv_files[0]
    print(f"Reading file: {f['name']}")
    raw_bytes = drive.get_file_bytes(f["id"])
    text = raw_bytes.decode("cp932")
    reader = csv.DictReader(io.StringIO(text))
    # Print headers
    print("Detected Headers:", reader.fieldnames)
    
    # Print first row
    first_row = next(reader)
    print("First Row Example:", first_row)

if __name__ == "__main__":
    check_mf_csv()
