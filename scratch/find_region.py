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

    regions = [
        "ap-northeast-1", # Tokyo
        "ap-northeast-2", # Seoul
        "ap-southeast-1", # Singapore
        "ap-southeast-2", # Sydney
        "us-east-1",      # N. Virginia
        "us-east-2",      # Ohio
        "us-west-1",      # N. California
        "us-west-2",      # Oregon
        "eu-central-1",   # Frankfurt
        "eu-west-1",      # Ireland
        "eu-west-2",      # London
        "eu-west-3",      # Paris
        "sa-east-1",      # Sao Paulo
        "ca-central-1"    # Canada
    ]

    for region in regions:
        host = f"aws-0-{region}.pooler.supabase.com"
        db_url = f"postgresql://postgres.hjkcgulxddtwlljhbocb:{db_password}@{host}:6543/postgres"
        print(f"Testing region: {region} ({host})...")
        try:
            conn = psycopg2.connect(db_url, connect_timeout=3)
            print(f"SUCCESS! Connected to {region}")
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE quiz_history ADD COLUMN IF NOT EXISTS source_name TEXT;")
                print("Column added successfully!")
            conn.close()
            return
        except psycopg2.OperationalError as e:
            err_msg = str(e)
            if "tenant/user" in err_msg and "not found" in err_msg:
                # This region does not host this project
                print(f"  -> Not this region (tenant not found)")
            else:
                # Other error (e.g., Auth failed, connection timeout, etc.)
                print(f"  -> Connection error (might be the right region but other error): {err_msg}")
        except Exception as e:
            print(f"  -> Other exception: {e}")

    print("Finished scanning regions. None was successful.")

if __name__ == "__main__":
    main()
