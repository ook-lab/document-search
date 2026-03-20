import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from db_client import get_db

load_dotenv()

def check_auto_exclude_logic():
    db = get_db()
    
    # 1. Rules
    rules = db.table("Kakeibo_Auto_Exclude_Rules").select("*").eq("is_active", True).execute().data
    print(f"Active Rules: {len(rules)}")
    for r in rules:
        print(f" Rule: {r.get('rule_name')} | Content: {r.get('content_pattern')} | Inst: {r.get('institution_pattern')}")
        
    # 2. Sample Transactions
    txs = db.table("Rawdata_BANK_transactions").select("*").limit(200).execute().data
    print(f"Checking {len(txs)} transactions...")
    
    matched = 0
    for t in txs:
        content = t.get('content') or ""
        inst = t.get('institution') or ""
        for r in rules:
            cp = r.get('content_pattern') or ""
            ip = r.get('institution_pattern') or ""
            if cp in content and ip in inst:
                print(f" MATCH! TX: {content} ({inst}) matches Rule: {r.get('rule_name')}")
                matched += 1
                break
    
    print(f"Total matched: {matched}")

if __name__ == "__main__":
    check_auto_exclude_logic()
