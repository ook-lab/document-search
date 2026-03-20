import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def get_schema():
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/"
    headers = {"apikey": os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')}
    resp = httpx.get(url, headers=headers)
    schema = resp.json()
    
    table = schema['definitions'].get('Kakeibo_Manual_Edits')
    if table:
        print("Columns for Kakeibo_Manual_Edits:")
        for col, details in table['properties'].items():
            print(f" - {col}: {details.get('type')} ({details.get('format')})")
    else:
        print("Table Kakeibo_Manual_Edits not found in definitions")
        # Try to find it in paths
        print("
Paths available:")
        for path in schema['paths'].keys():
            if 'Kakeibo_Manual_Edits' in path:
                print(f" - {path}")

if __name__ == "__main__":
    get_schema()
