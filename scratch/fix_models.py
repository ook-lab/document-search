import os
import re

def process_file(file_path):
    if not os.path.exists(file_path):
        return
    
    # Process both .py and .yaml files
    if not (file_path.endswith('.py') or file_path.endswith('.yaml') or file_path.endswith('.yml') or file_path.endswith('.html')):
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return

    original_content = content
    
    # Replace model names
    content = content.replace('gemini-2.0-flash-lite-preview-02-05', 'gemini-2.0-flash-lite-preview-02-05')
    content = content.replace('gemini-2.5-flash-lite', 'gemini-2.5-flash-lite')
    
    # Replace regions for vertexai init and genai Client
    content = re.sub(r'vertexai\.init\(\s*location=[\'\"]asia-northeast1[\'\"]\s*\)', 'vertexai.init(location=\"us-central1\")', content)
    content = re.sub(r'genai\.Client\(\s*vertexai=True,\s*location=[\'\"]asia-northeast1[\'\"]\s*\)', 'genai.Client(vertexai=True, location=\"us-central1\")', content)

    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {file_path}")

for root, dirs, files in os.walk(r'c:\Users\ookub\document-management-system'):
    if 'venv' in root or '.git' in root or '_runtime' in root:
        continue
    for file in files:
        process_file(os.path.join(root, file))
