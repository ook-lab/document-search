from dotenv import load_dotenv
from db_client import get_db

load_dotenv()

def check_m_existence():
    db = get_db()
    rules = db.table("Kakeibo_Auto_Exclude_Rules").select("*").eq("is_active", True).execute().data
    
    for r in rules:
        cp = r['content_pattern']
        # Find matching tx IDs
        txs = db.table("Rawdata_BANK_transactions").select("id, content").ilike("content", f"%{cp}%").limit(10).execute().data
        if not txs: continue
        
        ids = [t['id'] for t in txs]
        m_res = db.table("Kakeibo_Manual_Edits").select("*").in_("transaction_id", ids).execute()
        if m_res.data:
            print(f"Rule '{cp}' matches some transactions that HAVE manual edits:")
            for m in m_res.data:
                print(f"  TX_ID: {m['transaction_id']}, is_excluded in DB: {m['is_excluded']}")
        else:
            print(f"Rule '{cp}' matches transactions but NONE have manual edits.")

if __name__ == "__main__":
    check_m_existence()
