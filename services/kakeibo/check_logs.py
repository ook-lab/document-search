from config import SUPABASE_URL, SUPABASE_KEY
from db_client import get_db

def check_logs():
    db = get_db()
    res = db.table("99_lg_image_proc_log").select("*").order("processed_at", desc=True).limit(5).execute()
    print(f"Latest 5 logs:")
    for r in res.data:
        print(f" - {r['processed_at']}: {r['file_name']} -> {r['status']}")
        if r['status'] == 'failed':
            print(f"   Error: {r['error_message']}")

if __name__ == "__main__":
    check_logs()
