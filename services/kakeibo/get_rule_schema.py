import os
import httpx
import json
from dotenv import load_dotenv

load_dotenv()

def get_rule_schema():
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/"
    headers = {"apikey": os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY')}
    resp = httpx.get(url, headers=headers)
    schema = resp.json()
    
    props = schema['definitions'].get('Kakeibo_Auto_Exclude_Rules', {}).get('properties', {})
    print(json.dumps(props, indent=2))

if __name__ == "__main__":
    get_rule_schema()
