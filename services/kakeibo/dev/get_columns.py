import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def get_columns():
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/"
    headers = {"apikey": os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')}
    resp = httpx.get(url, headers=headers)
    schema = resp.json()
    
    props = schema['definitions']['Kakeibo_Auto_Exclude_Rules']['properties']
    for k in props.keys():
        print(f"Column: {k}")

if __name__ == "__main__":
    get_columns()
