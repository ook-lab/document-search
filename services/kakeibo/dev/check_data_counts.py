import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from db_client import get_db

load_dotenv()

def check_counts():
    db = get_db()
    
    # Tables to check
    tables = [
        "Rawdata_BANK_transactions",
        "Kakeibo_Manual_Edits",
        "Rawdata_RECEIPT_items",
        "Rawdata_RECEIPT_shops",
        "Kakeibo_Receipt_Links"
    ]
    
    for t in tables:
        try:
            res = db.table(t).select("*", count="exact").limit(0).execute()
            print(f"Table {t}: {res.count} rows")
            
            if t == "Rawdata_BANK_transactions":
                mf_res = db.table(t).select("*", count="exact").ilike("id", "MF-%").limit(0).execute()
                print(f"  - MF transactions: {mf_res.count}")
            elif t == "Kakeibo_Manual_Edits":
                mf_m = db.table(t).select("*", count="exact").ilike("transaction_id", "MF-%").limit(0).execute()
                print(f"  - MF edits: {mf_m.count}")
        except Exception as e:
            print(f"Error checking {t}: {e}")

if __name__ == "__main__":
    check_counts()
