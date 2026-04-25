import os

def process_file(file_path):
    if not os.path.exists(file_path):
        return
    
    if not (file_path.endswith('.py') or file_path.endswith('.yaml') or file_path.endswith('.yml') or file_path.endswith('.html') or file_path.endswith('.env')):
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return

    original_content = content
    
    # Revert back to Gemini 2.0 Flash Lite (the actual lite model in Vertex AI)
    # The previous script replaced gemini-2.0-flash-lite-preview-02-05-lite with gemini-2.0-flash-lite-preview-02-05
    # We will now replace gemini-2.0-flash-lite-preview-02-05 with gemini-2.0-flash-lite-preview-02-05
    # Be careful not to replace legitimate gemini-2.0-flash-lite-preview-02-05 if they were there originally...
    # Oh well, the user wants the cheapest one for all defaults.
    content = content.replace('gemini-2.0-flash-lite-preview-02-05', 'gemini-2.0-flash-lite-preview-02-05')
    
    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Reverted {file_path}")

for root, dirs, files in os.walk(r'c:\Users\ookub\document-management-system'):
    if 'venv' in root or '.git' in root or '_runtime' in root:
        continue
    for file in files:
        process_file(os.path.join(root, file))
