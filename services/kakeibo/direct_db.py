import httpx
import os
import json

def direct_insert(table_name: str, data: dict):
    url = f"{os.environ['SUPABASE_URL']}/rest/v1/{table_name}"
    headers = {
        "apikey": os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"],
        "Authorization": f"Bearer {os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    with httpx.Client() as client:
        response = client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()

if __name__ == "__main__":
    # Test it
    from config import DEFAULT_OWNER_ID
    test_data = {
        "receipt_id": "0907ddc0-4f79-4236-96c2-cb3d9811b2d0", # Existing test shop id
        "line_number": 999,
        "product_name": "DIRECT_REST_TEST",
        "quantity": 1,
        "owner_id": DEFAULT_OWNER_ID
    }
    try:
        res = direct_insert("Rawdata_RECEIPT_items", test_data)
        print(f"Direct Insert Success: {res}")
    except Exception as e:
        print(f"Direct Insert Failed: {e}")
        if hasattr(e, 'response'):
            print(f"Response: {e.response.text}")
