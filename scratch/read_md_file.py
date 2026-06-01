import re

path = r"H:\マイドライブ\shikakusui_setdan_complete_kaisetsu.md"
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Strip base64 image data to make it readable
    clean_content = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', '[BASE64 IMAGE DATA]', content)
    
    lines = clean_content.splitlines()
    for idx, line in enumerate(lines, 1):
        if line.strip():
            print(f"{idx}: {line}")
except Exception as e:
    print(f"Error: {e}")
