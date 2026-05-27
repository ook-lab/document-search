# -*- coding: utf-8 -*-
import sys
import requests
import json
import time

sys.stdout.reconfigure(encoding='utf-8')

pdf_path = r"H:\共有ドライブ\DataBase\Classroom\IKUYA-classroom\文書名学年通信 (50).pdf [1wOeCX5hPq4DK2T7zp1e_oyOkfATWrfbB]-3.pdf"
upload_url = "http://127.0.0.1:5055/pipeline-lab/api/upload"

# 1. Upload
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

# 2. Run Pipeline
run_url = f"http://127.0.0.1:5055/pipeline-lab/api/run/{session_id}/0"
print("Running pipeline...")
# The payload might contain settings or config. Let's keep it simple.
# Note: we need to ensure gemini-3.1-flash-lite or whatever is expected is used if specified,
# but normally it uses stage-configured models.
run_resp = requests.post(run_url, json={})
run_res = run_resp.json()
print("Run response:", json.dumps(run_res, ensure_ascii=False, indent=2))

if not run_res.get("success"):
    print("Pipeline run failed!")
    exit(1)

# 3. Get Result
result_url = f"http://127.0.0.1:5055/pipeline-lab/api/result/{session_id}/0"
print("Fetching result...")
res_resp = requests.get(result_url)
result_res = res_resp.json()

# Save output
if result_res.get("success"):
    # The result should contain the reconstructed markdown
    # Let's inspect where the markdown is located in the response
    print("Result structure keys:", result_res.keys())
    
    # We can write the entire JSON response to help debugging
    with open(r"C:\Users\ookub\document-management-system\scratch\pipeline_raw_result.json", "w", encoding="utf-8") as f:
        json.dump(result_res, f, ensure_ascii=False, indent=2)
        
    # Extract markdown text if available
    md_content = result_res.get("raw_md") or result_res.get("markdown") or ""
    if not md_content and "result" in result_res:
        md_content = result_res["result"].get("raw_md") or ""
        
    with open(r"C:\Users\ookub\document-management-system\scratch\pipeline_result.md", "w", encoding="utf-8") as f:
        f.write(md_content)
    print("Saved markdown result to pipeline_result.md")
else:
    print("Failed to get result!")
