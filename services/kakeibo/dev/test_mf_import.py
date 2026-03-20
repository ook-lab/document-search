import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import sys

def test_mf_import():
    url = "http://localhost:8080/api/mf_csv/import"
    try:
        response = requests.post(url)
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.json()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_mf_import()
