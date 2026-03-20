import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
import os
import httpx

sys.path.append(os.getcwd())

from config import SUPABASE_URL, SUPABASE_KEY, DEFAULT_OWNER_ID

def test_reconcile_fix():
    tx_id = "MF-xZTkVMcxNJ2v8uOafXhriPxYe2TUrkFbNf2mgv2iAYw"
    updates = [{
        "transaction_id": tx_id,
        "is_excluded": True,
        "note": "FIX TEST",
        "owner_id": DEFAULT_OWNER_ID
    }]
    
    url = f"{SUPABASE_URL}/rest/v1/Kakeibo_Manual_Edits"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal, resolution=merge-duplicates"
    }
    
    try:
        with httpx.Client() as client:
            resp = client.post(url, headers=headers, json=updates)
            print("Status Code:", resp.status_code)
            print("Response:", resp.text)
            resp.raise_for_status()
    except Exception as e:
        print("Upsert FAILED")

if __name__ == "__main__":
    test_reconcile_fix()
