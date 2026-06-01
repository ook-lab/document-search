import os
import sys
import io
from pathlib import Path
from dotenv import load_dotenv

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "services" / "sansu-base"))

load_dotenv(repo_root / ".env")

# Prevent CP932 encoding errors in terminal
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from app import app
client = app.test_client()

# Create a small dummy image in memory
from PIL import Image
img = Image.new('RGB', (100, 100), color = 'white')
img_byte_arr = io.BytesIO()
img.save(img_byte_arr, format='PNG')
img_byte_arr.seek(0)

print("\n--- Testing POST /api/ocr/read-problem with mock image ---")
try:
    response = client.post(
        '/api/ocr/read-problem',
        data={
            'hint': 'Test hint',
            'model': 'gemini-3.5-flash',
            'session_id': 'test-session-id',
            'file': (img_byte_arr, 'test_image.png')
        },
        content_type='multipart/form-data'
    )
    print(f"Status Code: {response.status_code}")
    print(f"Data: {response.get_data(as_text=True)}")
except Exception as e:
    import traceback
    traceback.print_exc()
