import requests
import json

BASE_URL = "http://127.0.0.1:5000"

def test_get_subjects():
    print("Testing GET /api/subjects...")
    res = requests.get(f"{BASE_URL}/api/subjects")
    if res.status_code == 200:
        subjects = res.json()
        print(f"Success! Found {len(subjects)} subjects.")
        for s in subjects:
            print(f" - {s['name']} (id: {s['id']}, sort_order: {s.get('sort_order')})")
        return subjects
    else:
        print(f"Failed! Status: {res.status_code}, Body: {res.text}")
        return None

def test_reorder_subjects(subjects):
    if not subjects or len(subjects) < 2:
        print("Not enough subjects to test reorder.")
        return
        
    print("\nTesting POST /api/subjects/reorder...")
    # 順序を反転させてテストしてみる
    reordered_payload = []
    for idx, sub in enumerate(reversed(subjects)):
        reordered_payload.append({
            "id": sub["id"],
            "sort_order": idx + 1
        })
        
    res = requests.post(f"{BASE_URL}/api/subjects/reorder", json=reordered_payload)
    if res.status_code == 200:
        print("Success! Reordered subjects.")
        # もう一度GETして順序を確認
        print("\nVerifying reordered subjects via GET...")
        res_get = requests.get(f"{BASE_URL}/api/subjects")
        reordered_subjects = res_get.json()
        for s in reordered_subjects:
            print(f" - {s['name']} (id: {s['id']}, sort_order: {s.get('sort_order')})")
            
        # 元の順序に戻す
        print("\nRestoring original order...")
        restore_payload = []
        for idx, sub in enumerate(subjects):
            restore_payload.append({
                "id": sub["id"],
                "sort_order": sub.get("sort_order", idx + 1)
            })
        requests.post(f"{BASE_URL}/api/subjects/reorder", json=restore_payload)
        print("Original order restored.")
    else:
        print(f"Failed! Status: {res.status_code}, Body: {res.text}")

if __name__ == "__main__":
    subjects = test_get_subjects()
    if subjects:
        test_reorder_subjects(subjects)
