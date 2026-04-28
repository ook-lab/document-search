import os
import re

def process_file(file_path):
    if not os.path.exists(file_path):
        return
    
    # We only touch .py, .yaml, .yml
    if not (file_path.endswith('.py') or file_path.endswith('.yaml') or file_path.endswith('.yml') or file_path.endswith('.env')):
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return

    original_content = content
    
    # 1. Replace gemini-2.5-flash-lite (but NOT gemini-2.5-flash-lite) with gemini-2.5-flash-lite
    content = re.sub(r'gemini-2\.5-flash(?!-lite)', 'gemini-2.5-flash-lite', content)
    
    # 2. Replace gemini-2.5-flash-lite with gemini-2.5-flash-lite
    content = content.replace('gemini-2.5-flash-lite', 'gemini-2.5-flash-lite')
    
    # 3. Replace location="asia-northeast1" for vertexai and genai.Client
    # Only replace if it's part of an init or Client call
    content = re.sub(r'vertexai\.init\(\s*location=[\'\"]asia-northeast1[\'\"]\s*\)', 'vertexai.init(location=os.environ.get("VERTEX_AI_REGION", "us-central1"))', content)
    content = re.sub(r'genai\.Client\(\s*vertexai=True,\s*location=[\'\"]asia-northeast1[\'\"]\s*\)', 'genai.Client(vertexai=True, location=os.environ.get("VERTEX_AI_REGION", "us-central1"))', content)
    
    # Add 'import os' if we added os.environ but it's not present in the file
    if content != original_content and 'os.environ.get' in content and 'import os' not in content:
        # We find the first import and put it there, or top of file
        content = "import os\n" + content

    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated: {file_path}")

base_dir = r'c:\Users\ookub\document-management-system'
for root, dirs, files in os.walk(base_dir):
    if 'venv' in root or '.git' in root or '_runtime' in root:
        continue
    for file in files:
        process_file(os.path.join(root, file))
