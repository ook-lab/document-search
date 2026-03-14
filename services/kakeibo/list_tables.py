import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def list_tables():
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/"
    headers = {"apikey": os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')}
    resp = httpx.get(url, headers=headers)
    schema = resp.json()
    
    print("Relevant Tables found in schema:")
    tables = sorted(schema['definitions'].keys())
    for table_name in tables:
        if 'Kakeibo' in table_name or 'Receipt' in table_name:
            print(f" - {table_name}")
        elif 'Rawdata' in table_name:
            print(f" - {table_name}")

if __name__ == "__main__":
    list_tables()
