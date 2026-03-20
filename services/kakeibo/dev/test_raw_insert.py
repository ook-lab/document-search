import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY, DEFAULT_OWNER_ID
from db_client import get_db

def test_raw_insert():
    db = get_db()
    print(f"Testing insert with DEFAULT_OWNER_ID: {DEFAULT_OWNER_ID}")
    
    # Test shops table
    shop_data = {
        "transaction_date": "2026-03-14",
        "shop_name": "TEST_SHOP",
        "total_amount_check": 1000,
        "owner_id": DEFAULT_OWNER_ID,
        "workspace": "household"
    }
    try:
        res = db.table("Rawdata_RECEIPT_shops").insert(shop_data).execute()
        shop_id = res.data[0]["id"]
        print(f"Shop insert SUCCESS: {shop_id}")
        
        # Test items table
        item_data = {
            "receipt_id": shop_id,
            "line_number": 1,
            "product_name": "TEST_ITEM_NO_OWNER",
            "quantity": 1,
            # "owner_id": DEFAULT_OWNER_ID
        }
        res2 = db.table("Rawdata_RECEIPT_items").insert(item_data).execute()
        print(f"Item insert SUCCESS: {res2.data[0]['id']}")
        
    except Exception as e:
        print(f"FAILED_START
{e}
FAILED_END")

if __name__ == "__main__":
    test_raw_insert()
