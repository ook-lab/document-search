import os
import re

def refactor_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    
    # 1. Replace google.generativeai with vertexai
    if 'import google.generativeai as genai' in content:
        content = content.replace(
            'import google.generativeai as genai',
            'import vertexai\nfrom vertexai.generative_models import GenerativeModel, Part, GenerationConfig'
        )
    
    if 'import google.generativeai as genai_mod' in content:
        content = content.replace(
            'import google.generativeai as genai_mod',
            'import vertexai\nfrom vertexai.generative_models import GenerativeModel, Part, GenerationConfig'
        )

    # 2. Replace genai.configure(api_key=...)
    content = re.sub(
        r'genai\.configure\(api_key=.*?\)',
        r'vertexai.init(location="asia-northeast1")',
        content
    )

    # 3. Replace genai.GenerativeModel(...)
    content = content.replace('genai.GenerativeModel(', 'GenerativeModel(')
    content = content.replace('genai_mod.GenerativeModel(', 'GenerativeModel(')

    # 4. Replace genai.types.GenerationConfig or genai.GenerationConfig
    content = content.replace('genai.types.GenerationConfig(', 'GenerationConfig(')
    content = content.replace('genai.GenerationConfig(', 'GenerationConfig(')

    # 5. Fix `response_mime_type` inside dicts if any
    
    # 6. google-genai replacements (for genai.Client)
    if 'genai.Client(' in content:
        content = content.replace('from google import genai', 'import vertexai\nfrom google import genai')
        content = re.sub(
            r'genai\.Client\(api_key=.*?\)',
            r'genai.Client(vertexai=True, location="asia-northeast1")',
            content
        )

    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Refactored {file_path}")

def main():
    base_dir = r"c:\Users\ookub\document-management-system"
    for root, dirs, files in os.walk(base_dir):
        if 'venv' in root or '.git' in root or '_runtime' in root:
            continue
        for file in files:
            if file.endswith('.py'):
                # Avoid modifying the script itself if it's placed inside
                if file == 'refactor.py':
                    continue
                file_path = os.path.join(root, file)
                try:
                    refactor_file(file_path)
                except Exception as e:
                    print(f"Error processing {file_path}: {e}")

if __name__ == '__main__':
    main()
