# -*- coding: utf-8 -*-
import requests
import json

url = "http://127.0.0.1:5055/pipeline-lab/api/extract_direct/93c39e5fe065/0"
data = {
    "model": "gemini-3.1-flash-lite"
}

response = requests.post(url, json=data)
result = response.json()

print(json.dumps(result, ensure_ascii=False, indent=2))

# Save the markdown result to a scratch file
if result.get("success"):
    with open(r"C:\Users\ookub\document-management-system\scratch\extract_result_3_1.md", "w", encoding="utf-8") as f:
        f.write(result.get("markdown", ""))
    print("SAVED TO extract_result_3_1.md")
