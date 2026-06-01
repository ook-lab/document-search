import os
import sys
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

def main():
    # ワークスペースルートのパスを追加
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    load_dotenv(repo_root / ".env")

    db_password = os.getenv("password")
    if not db_password:
        print("Error: '.env' file does not contain db password ('password')")
        sys.exit(1)

    db_url = f"postgresql://postgres:{db_password}@db.hjkcgulxddtwlljhbocb.supabase.co:5432/postgres"
    migration_file = repo_root / "supabase" / "migrations" / "20260530000000_create_math_problems.sql"

    if not migration_file.exists():
        print(f"Error: Migration file not found: {migration_file}")
        sys.exit(1)

    print(f"Reading migration file: {migration_file.name}")
    with open(migration_file, "r", encoding="utf-8") as f:
        sql = f.read()

    print("Connecting to Supabase PostgreSQL database...")
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            print("Executing migration SQL...")
            cur.execute(sql)
            print("Migration executed successfully!")
        conn.close()
    except Exception as e:
        print(f"Database connection or execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
