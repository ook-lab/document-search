import os
import sys
from pathlib import Path

# Add project root and services/sansu-base to sys.path
project_root = Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))
sys.path.append(str(project_root / "services" / "sansu-base"))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from dms.common.database.client import DatabaseClient

db = DatabaseClient(use_service_role=True)
res = db.client.table("math_problems").select("*").execute()

print(f"Total problems found: {len(res.data)}")
for prob in res.data:
    p_text = prob.get("problem_markdown") or ""
    e_text = prob.get("explanation_markdown") or ""
    
    print(f"\n==========================================")
    print(f"ID: {prob.get('display_id')} (UUID: {prob.get('id')})")
    print(f"Source: {prob.get('source_book')} / {prob.get('chapter')}")
    print(f"-------------------- PROBLEM --------------------")
    print(p_text)
    print(f"-------------------- EXPLANATION --------------------")
    print(e_text)
