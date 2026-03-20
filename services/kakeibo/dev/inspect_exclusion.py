import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from db_client import get_db

load_dotenv()

def find_mismatch():
    db = get_db()
    rules = db.table("Kakeibo_Auto_Exclude_Rules").select("*").eq("is_active", True).execute().data
    
    # Fetch some transactions and their manual edits
    txs = db.table("Rawdata_BANK_transactions").select("*").limit(500).execute().data
    ids = [t['id'] for t in txs]
    m_res = db.table("Kakeibo_Manual_Edits").select("*").in_("transaction_id", ids).execute()
    manual_map = {m['transaction_id']: m for m in m_res.data}
    
    count = 0
    for t in txs:
        content = t.get('content') or ""
        inst = t.get('institution') or ""
        m = manual_map.get(t['id'])
        
        is_auto = False
        for r in rules:
            if (r.get('content_pattern') or "") in content and (r.get('institution_pattern') or "") in inst:
                is_auto = True
                break
        
        if is_auto:
            is_manual_ex = m.get('is_excluded') if m else None
            print(f"Match: {content} | Auto: True | Manual: {is_manual_ex}")
            count += 1
            if count > 20: break

if __name__ == "__main__":
    find_mismatch()
