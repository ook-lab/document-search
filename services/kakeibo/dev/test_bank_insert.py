import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
import os
import uuid

sys.path.append(os.getcwd())

from db_client import get_db
from config import DEFAULT_OWNER_ID

def test_bank_insert():
    db = get_db()
    new_id = f"TEST-{uuid.uuid4()}"
    data = {
        "id": new_id,
        "date": "2026-03-14",
        "content": "TEST BANK INSERT",
        "amount": 100,
        "owner_id": DEFAULT_OWNER_ID
    }
    
    try:
        res = db.table("Rawdata_BANK_transactions").insert(data).execute()
        print("Bank Insert SUCCESS:", res.data)
    except Exception as e:
        print("Bank Insert FAILED:", e)

if __name__ == "__main__":
    test_bank_insert()
