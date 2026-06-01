import sys
from pathlib import Path

app_path = Path("services/sansu-base/app.py")
if not app_path.exists():
    print("app.py not found")
    sys.exit(1)

content = app_path.read_text(encoding="utf-8")
lines = content.splitlines()

# 1. Remove convert_pdf_to_images helper (lines 19 to 33, 1-indexed, which is index 18 to 32)
# Wait, let's double check lines 19 to 33:
print("Removing helper lines:")
for idx in range(18, 33):
    print(f"  {idx+1}: {lines[idx]}")

# 2. Remove OCR & Import endpoints (lines 913 to 1318, 1-indexed, which is index 912 to 1317)
print("Removing endpoints lines:")
for idx in range(912, 915):
    print(f"  {idx+1}: {lines[idx]}")
print("...")
for idx in range(1315, 1318):
    print(f"  {idx+1}: {lines[idx]}")

# Let's perform the slicing:
new_lines = lines[:18] + lines[33:912] + lines[1318:]

app_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
print("Successfully cleaned up app.py!")
