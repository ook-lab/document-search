import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def get_schema_all():
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/"
    headers = {"apikey": os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')}
    resp = httpx.get(url, headers=headers)
    schema = resp.json()
    
    for tname in ['Rawdata_BANK_transactions', 'Kakeibo_Manual_Edits']:
        table = schema['definitions'].get(tname)
        if table:
            print(f"Columns for {tname}:")
            cols = table['properties'].keys()
            print(", ".join(cols))
        else:
            print(f"Table {tname} not found")

if __name__ == "__main__":
    get_schema_all()
