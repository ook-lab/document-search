from config import SUPABASE_URL, SUPABASE_KEY
from db_client import get_db

def check_db():
    db = get_db()
    res = db.table("Rawdata_RECEIPT_shops").select("id, shop_name, created_at").order("created_at", desc=True).limit(5).execute()
    print(f"Latest 5 receipts:")
    for r in res.data:
        print(f" - {r['created_at']}: {r['shop_name']} ({r['id']})")

if __name__ == "__main__":
    check_db()
