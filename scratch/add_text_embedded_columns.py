"""
Supabase Management API を使って DDL を直接実行するスクリプト
"""
import requests
import os

# Supabase の project_ref と service_role_key
PROJECT_REF = "hjkcgulxddtwlljhbocb"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imhqa2NndWx4ZGR0d2xsamhib2NiIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzgzNTU3MywiZXhwIjoyMDc5NDExNTczfQ.u3NCUzE1Sz_gsu1EDCndlDwFNBVa45-UQdkdWlOa6g8"

# Supabase DB REST endpoint for raw SQL
DB_URL = f"https://{PROJECT_REF}.supabase.co/rest/v1/rpc"

headers = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# psql 経由でなく、Supabase pg REST を使う
# 各 ALTER 文を個別のリクエストで送る
from supabase import create_client
sb = create_client(f"https://{PROJECT_REF}.supabase.co", SERVICE_KEY)

def run_ddl(description, sql):
    """Supabase の pg 経由で DDL 実行"""
    try:
        # supabase-py の PostgrestClient では DDL は使えないので
        # requests で直接 pg/query を叩く
        url = f"https://{PROJECT_REF}.supabase.co/pg/query"
        res = requests.post(
            url,
            headers={**headers, "Content-Profile": "public"},
            json={"query": sql}
        )
        if res.status_code in [200, 204]:
            print(f"  OK: {description}")
            return True
        else:
            print(f"  FAIL ({res.status_code}): {description}")
            print(f"       {res.text[:200]}")
            return False
    except Exception as e:
        print(f"  ERROR: {description} - {e}")
        return False

migrations = [
    ("pipeline_meta: text_embedded",
     "ALTER TABLE pipeline_meta ADD COLUMN IF NOT EXISTS text_embedded BOOLEAN DEFAULT FALSE"),
    ("pipeline_meta: text_embedded_at",
     "ALTER TABLE pipeline_meta ADD COLUMN IF NOT EXISTS text_embedded_at TIMESTAMPTZ"),
    ("pipeline_meta: drive_file_id",
     "ALTER TABLE pipeline_meta ADD COLUMN IF NOT EXISTS drive_file_id TEXT"),
    ("09_unified_documents: text_embedded",
     'ALTER TABLE "09_unified_documents" ADD COLUMN IF NOT EXISTS text_embedded BOOLEAN DEFAULT FALSE'),
    ("09_unified_documents: text_embedded_at",
     'ALTER TABLE "09_unified_documents" ADD COLUMN IF NOT EXISTS text_embedded_at TIMESTAMPTZ'),
    ("index: pipeline_meta text_embedded",
     "CREATE INDEX IF NOT EXISTS idx_pm_text_embedded ON pipeline_meta(text_embedded) WHERE text_embedded = TRUE"),
    ("index: pipeline_meta drive_file_id",
     "CREATE INDEX IF NOT EXISTS idx_pm_drive_file_id ON pipeline_meta(drive_file_id) WHERE drive_file_id IS NOT NULL"),
]

print("=== Running migrations ===")
for name, sql in migrations:
    run_ddl(name, sql)

# 確認
print("\n=== Verify pipeline_meta columns ===")
r = sb.table("pipeline_meta").select("*").limit(1).execute()
if r.data:
    cols = list(r.data[0].keys())
    for c in ["text_embedded", "text_embedded_at", "drive_file_id"]:
        found = "OK" if c in cols else "MISSING"
        print(f"  {found}: {c}")
