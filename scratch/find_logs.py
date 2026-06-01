import json
from pathlib import Path

log_path = Path("C:/Users/ookub/.gemini/antigravity/brain/deb5267b-5afe-4717-9bf9-52d97434d42f/.system_generated/logs/transcript.jsonl")

if not log_path.exists():
    print("Log file not found")
else:
    print("Searching log file...")
    count = 0
    with open(log_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            if line_num == 700:
                data = json.loads(line)
                print(data.get("content", ""))
                break
