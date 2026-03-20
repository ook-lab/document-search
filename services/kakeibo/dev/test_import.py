import sys
import os

sys.path.append("c:/Users/ookub/document-management-system/services/kakeibo")

from app import app

def test_import():
    with app.test_client() as client:
        print("Testing /api/receipts/import ...")
        response = client.post('/api/receipts/import')
        print(f"Status Code: {response.status_code}")
        print(f"Response data: {response.get_json()}")

if __name__ == "__main__":
    test_import()
