import sys
from pathlib import Path
from dotenv import load_dotenv

# Set PYTHONPATH
_here = Path(__file__).resolve().parent
_repo = _here.parent
_sansu_base = _repo / "services" / "sansu-base"
if str(_sansu_base) not in sys.path:
    sys.path.insert(0, str(_sansu_base))
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

load_dotenv(_repo / ".env")

from dms.common.database.client import DatabaseClient

def cleanup():
    db = DatabaseClient(use_service_role=True)
    
    # Clean up test display IDs
    test_ids = ["TEST-HIST-001", "TEST-HIST-002", "TEST-HIST-003", "GEO-002"]
    
    for display_id in test_ids:
        print(f"Deleting display_id: {display_id}")
        res = db.client.table("math_problems").delete().eq("display_id", display_id).execute()
        print(f"Deleted records: {res.data}")

if __name__ == "__main__":
    cleanup()
