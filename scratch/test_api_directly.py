import os
import sys
import traceback
from pathlib import Path
from dotenv import load_dotenv

# Add repo root to path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "services" / "sansu-base"))

load_dotenv(repo_root / ".env")

from app import app

# Set Python output encoding to utf-8 to prevent CP932 errors
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

client = app.test_client()

def test_endpoint(path):
    print(f"\n--- Testing GET {path} ---")
    try:
        response = client.get(path)
        print(f"Status Code: {response.status_code}")
        print(f"Data: {response.get_data(as_text=True)[:200]}...")
    except Exception as e:
        print("Exception occurred:")
        traceback.print_exc()

test_endpoint('/api/drive/files')
test_endpoint('/api/problems')
test_endpoint('/api/history/source-books')
test_endpoint('/api/problems/next-id?unit=平面図形')
test_endpoint('/reader')


