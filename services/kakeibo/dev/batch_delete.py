import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from db_client import get_db
import httpx
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

def delete_in_batches():
    db = get_db()
    
    # 1. Get all MF IDs
    res = db.table("Rawdata_BANK_transactions").select("id").ilike("id", "MF-%").execute()
    mf_ids = [r['id'] for r in res.data]
    print(f"Found {len(mf_ids)} MF transactions to delete.")
    
    # 2. Delete them in batches
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    
    batch_size = 100
    for i in range(0, len(mf_ids), batch_size):
        chunk = mf_ids[i:i+batch_size]
        # Build CSV string for IN filter
        id_list = ",".join([f'"{id}"' for id in chunk])
        url = f"{SUPABASE_URL}/rest/v1/Rawdata_BANK_transactions?id=in.({id_list})"
        
        try:
            resp = httpx.delete(url, headers=headers)
            print(f"Batch {i//batch_size + 1}: Deleted {len(chunk)} rows. Status: {resp.status_code}")
            if resp.status_code >= 400:
                print(f"Error: {resp.text}")
        except Exception as e:
            print(f"Error in batch {i}: {e}")

if __name__ == "__main__":
    delete_in_batches()
