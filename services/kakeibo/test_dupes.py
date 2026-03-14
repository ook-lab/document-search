from dotenv import load_dotenv
from db_client import get_db

load_dotenv()

def check_duplicates():
    db = get_db()
    
    # Let's find some existing IDs starting with MF-
    res = db.table("Rawdata_BANK_transactions").select("id").like("id", "MF-%").limit(10).execute()
    existing_ids = [r["id"] for r in res.data]
    print(f"Existing IDs found: {existing_ids}")
    
    if not existing_ids:
        print("No MF- IDs found to test.")
        return
        
    # Check if we can find them via .in_()
    check_res = db.table("Rawdata_BANK_transactions").select("id").in_("id", existing_ids).execute()
    found_ids = [r["id"] for r in check_res.data]
    print(f"Found via .in_(): {found_ids}")
    
    if len(existing_ids) == len(found_ids):
        print("Duplicate check mechanism works for these IDs.")
    else:
        print(f"MISMATCH! Expected {len(existing_ids)}, found {len(found_ids)}")

if __name__ == "__main__":
    check_duplicates()
