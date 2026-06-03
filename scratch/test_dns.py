import socket
import sys

def main():
    hosts = [
        "db.hjkcgulxddtwlljhbocb.supabase.co",
        "aws-0-ap-northeast-1.pooler.supabase.com",
        "aws-0-ap-southeast-1.pooler.supabase.com",
        "aws-0-us-east-1.pooler.supabase.com"
    ]
    for host in hosts:
        print(f"--- Resolving {host} ---")
        try:
            info = socket.getaddrinfo(host, 5432)
            for item in info:
                print(item)
        except Exception as e:
            print(f"Failed to resolve {host}: {e}")

if __name__ == "__main__":
    main()
