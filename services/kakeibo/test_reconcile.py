import sys
import os

sys.path.append(os.getcwd())

from db_client import get_db
from config import DEFAULT_OWNER_ID

def test_reconcile():
    db = get_db()
    # Find a sample transaction ID
    res = db.table("Rawdata_BANK_transactions").select("id").limit(1).execute()
    if not res.data:
        print("No transactions found")
        return
    
    tx_id = res.data[0]['id']
    print(f"Testing with tx_id: {tx_id}")
    
    updates = [{
        "transaction_id": tx_id,
        "is_excluded": True,
        "note": "TEST RECONCILE",
        "owner_id": DEFAULT_OWNER_ID
    }]
    
    try:
        res_upsert = db.table("Kakeibo_Manual_Edits").upsert(updates, on_conflict="transaction_id").execute()
        print("Upsert SUCCESS:", res_upsert.data)
        
        # Verify
        res_check = db.table("Kakeibo_Manual_Edits").select("*").eq("transaction_id", tx_id).execute()
        print("Check Result:", res_check.data)
        
    except Exception as e:
        print("Upsert FAILED:", e)

if __name__ == "__main__":
    test_reconcile()
