import os
import httpx
from dotenv import load_dotenv
import sys

load_dotenv()

def get_columns(table_name):
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/"
    headers = {"apikey": os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')}
    resp = httpx.get(url, headers=headers)
    schema = resp.json()
    
    props = schema['definitions'].get(table_name, {}).get('properties', {})
    if not props:
        print(f"Table {table_name} not found or has no props.")
        return
    print(f"Columns for {table_name}:")
    for k in props.keys():
        print(f" - {k}")

if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else "Rawdata_RECEIPT_items"
    get_columns(t)
