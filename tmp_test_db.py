
import sys
from pathlib import Path
root_dir = Path("C:/Users/ookub/document-management-system")
sys.path.insert(0, str(root_dir))

try:
    from dotenv import load_dotenv
    load_dotenv(root_dir / ".env")
    from shared.common.database.client import DatabaseClient
    db = DatabaseClient(use_service_role=True)
    print("Database client initialized successfully")
    # Test a simple query
    res = db.client.table('pipeline_meta').select('count', count='exact').limit(1).execute()
    print(f"Query successful: {res.count} records found")
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
