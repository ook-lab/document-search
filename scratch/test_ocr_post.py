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

# Sample image path
sample_img_path = Path(r"C:\Users\ookub\.gemini\antigravity\brain\6ffd9925-4da3-4b49-8789-c00ec58f0c55\math_problem_sample_1781319185319.png")

if not sample_img_path.exists():
    print(f"Error: Sample image not found at {sample_img_path}")
    sys.exit(1)

print("\n--- Testing POST /api/ocr/read-problem with generated math problem image ---")
try:
    with open(sample_img_path, 'rb') as f:
        img_bytes = f.read()
        
    response = client.post(
        '/api/ocr/read-problem',
        data={
            'hint': '直角三角形の面積問題。底辺が4cm、高さが3cmです。',
            'model': 'gemini-3.5-flash',
            'session_id': 'test-session-id',
            'file': (io.BytesIO(img_bytes), 'math_problem_sample.png')
        },
        content_type='multipart/form-data'
    )
    print(f"Status Code: {response.status_code}")
    print(f"Response Data:\n{response.get_data(as_text=True)}")
except Exception as e:
    import traceback
    traceback.print_exc()
