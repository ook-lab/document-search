import os
import io
import csv
from dotenv import load_dotenv
from drive_client import DriveClient

load_dotenv()

def inspect_file(file_id):
    drive = DriveClient()
    print(f"Inspecting file: {file_id}")
    raw_bytes = drive.get_file_bytes(file_id)
    if raw_bytes is None:
        print("Failed to get file bytes.")
        return
    text = raw_bytes.decode("cp932")
    reader = csv.DictReader(io.StringIO(text))
    print("Headers:", reader.fieldnames)
    
    row = next(reader)
    print("First Row:", row)

if __name__ == "__main__":
    # Use the ID found: 147mXwWNBg99mwkEyXbD7cdQv6olEEGC
    inspect_file("147mXwWNBg99mwkEyXbD7cdQv6olEEGC")
