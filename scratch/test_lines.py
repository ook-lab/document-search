import sys
from pathlib import Path

app_path = Path("services/sansu-base/app.py")
if not app_path.exists():
    print("app.py not found")
    sys.exit(1)

lines = app_path.read_text(encoding="utf-8").splitlines()
print(f"Total lines: {len(lines)}")
# Show lines around 913 (1-indexed is index 912)
print("--- 910 to 915 (0-indexed) ---")
for idx in range(910, 916):
    if idx < len(lines):
        print(f"{idx+1}: {lines[idx]}")

print("--- 1315 to 1325 (0-indexed) ---")
for idx in range(1315, 1325):
    if idx < len(lines):
        print(f"{idx+1}: {lines[idx]}")
