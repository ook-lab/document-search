import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from db_client import get_db
import httpx
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

def delete_data():
    db = get_db()
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    print("--- Starting Data Cleanup ---")

    # 1. Links and Logs (Full) with filters to satisfy PostgREST
    for table in ["Kakeibo_Receipt_Links", "99_lg_image_proc_log", "Rawdata_RECEIPT_items", "Rawdata_RECEIPT_shops"]:
        # IDがnullでないものを消す（実質すべて）
        url = f"{SUPABASE_URL}/rest/v1/{table}?id=not.is.null"
        try:
            resp = httpx.delete(url, headers=headers)
            print(f"Deleted {table}: {resp.status_code}")
        except Exception as e:
            print(f"Error deleting {table}: {e}")

    # 2. Manual Edits (FULL WIPEOUT)
    try:
        url_all_m = f"{os.getenv('SUPABASE_URL')}/rest/v1/Kakeibo_Manual_Edits?transaction_id=not.is.null"
        resp_all_m = httpx.delete(url_all_m, headers=headers)
        print(f"Deleted ALL Manual Edits: {resp_all_m.status_code}")
    except Exception as e:
        print(f"Error deleting Manual Edits: {e}")

    # 3. Raw Transactions (FULL WIPEOUT)
    try:
        url_all_raw = f"{os.getenv('SUPABASE_URL')}/rest/v1/Rawdata_BANK_transactions?id=not.is.null"
        resp_all_raw = httpx.delete(url_all_raw, headers=headers)
        print(f"Deleted ALL Raw transactions: {resp_all_raw.status_code}")
    except Exception as e:
        print(f"Error deleting Raw transactions: {e}")

    print("--- Cleanup Complete ---")

if __name__ == "__main__":
    delete_data()
