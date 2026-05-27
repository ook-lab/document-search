# -*- coding: utf-8 -*-
import requests
import json
import os

pdf_path = r"H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom\文書名５月ほけんだより山元.pdf [1bBj2jfZkZLBw8zw0ENo1SWDCwlWrnehN].pdf"
upload_url = "http://127.0.0.1:5055/pipeline-lab/api/upload"

print("Uploading PDF...")
with open(pdf_path, 'rb') as f:
    files = {'file': f}
    resp = requests.post(upload_url, files=files)

upload_res = resp.json()
print("Upload response:", json.dumps(upload_res, ensure_ascii=False, indent=2))
if not upload_res.get("success"):
    print("Upload failed!")
    exit(1)

session_id = upload_res["session_id"]
print(f"Session ID: {session_id}")

# Run AI Direct Extract
extract_url = f"http://127.0.0.1:5055/pipeline-lab/api/extract_direct/{session_id}/0"
print("Running AI Direct Extract...")
extract_resp = requests.post(extract_url, json={"model": "gemini-2.5-flash-lite"})
extract_res = extract_resp.json()

print("Extract response:", json.dumps(extract_res, ensure_ascii=False, indent=2))

if not extract_res.get("success"):
    print("Direct extraction failed!")
    exit(1)

# Save result markdown
markdown = extract_res.get("markdown") or ""
result_path = r"C:\Users\ookub\document-management-system\scratch\direct_extract_hoken_result.md"
with open(result_path, "w", encoding="utf-8") as f:
    f.write(markdown)
print(f"Saved direct extract markdown to: {result_path}")
