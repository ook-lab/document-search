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

    ipv6_addr = "2406:da14:271:9900:b155:8924:9ffa:d77e"
    db_url = f"postgresql://postgres:{db_password}@[{ipv6_addr}]:5432/postgres"

    print(f"Connecting to Supabase PostgreSQL database via IPv6 literal {ipv6_addr}...")
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            print("Executing SQL: ALTER TABLE quiz_history ADD COLUMN IF NOT EXISTS source_name TEXT;")
            cur.execute("ALTER TABLE quiz_history ADD COLUMN IF NOT EXISTS source_name TEXT;")
            print("Column added successfully!")
        conn.close()
    except Exception as e:
        print(f"Database connection or execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
