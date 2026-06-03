import os
import sys
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

def main():
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    load_dotenv(repo_root / ".env")

    db_password = os.getenv("password")
    if not db_password:
        print("Error: '.env' file does not contain db password ('password')")
        sys.exit(1)

    project_ref = "hjkcgulxddtwlljhbocb"
    # Supabaseのプール接続ホスト (IPv4対応)
    db_host = "aws-0-ap-northeast-1.pooler.supabase.com"
    db_user = f"postgres.{project_ref}"
    db_port = 5432
    db_name = "postgres"

    print(f"Connecting to Supabase PostgreSQL database via Pooler ({db_host})...")
    try:
        conn = psycopg2.connect(
            host=db_host,
            database=db_name,
            user=db_user,
            password=db_password,
            port=db_port
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            print("Executing ALTER TABLE to add source_name column...")
            cur.execute("ALTER TABLE public.quiz_history ADD COLUMN IF NOT EXISTS source_name TEXT;")
            print("Successfully added source_name column to public.quiz_history!")
        conn.close()
    except Exception as e:
        print(f"Database connection or execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
