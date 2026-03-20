import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from db_client import get_db

load_dotenv()

def inspect_matching():
    db = get_db()
    rules = db.table("Kakeibo_Auto_Exclude_Rules").select("*").eq("is_active", True).execute().data
    
    print("Checking Rules and Transactions...")
    for r in rules:
        cp = r.get('content_pattern', '')
        # Search for transactions that contain this pattern
        txs = db.table("Rawdata_BANK_transactions").select("content, institution").ilike("content", f"%{cp}%").limit(5).execute().data
        if txs:
            print(f"Rule: '{cp}' matches {len(txs)} txs (showing first): '{txs[0]['content']}'")
        else:
             print(f"Rule: '{cp}' has no matches via ilike")

if __name__ == "__main__":
    inspect_matching()
