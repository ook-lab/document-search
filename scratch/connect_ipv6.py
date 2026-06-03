import os
import sys
import socket
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

    host = "db.hjkcgulxddtwlljhbocb.supabase.co"
    print("Resolving IPv6 address for host...")
    try:
        addr_info = socket.getaddrinfo(host, 5432, socket.AF_INET6)
        ipv6_addr = addr_info[0][4][0]
        print(f"Resolved IPv6 address: {ipv6_addr}")
    except Exception as e:
        print(f"Failed to resolve IPv6: {e}")
        sys.exit(1)

    db_url = f"postgresql://postgres:{db_password}@[{ipv6_addr}]:5432/postgres"

    print("Connecting to Supabase PostgreSQL database via IPv6...")
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
