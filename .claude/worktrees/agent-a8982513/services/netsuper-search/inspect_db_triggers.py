"""
データベースのトリガーとFunction定義を確認
"""
import os
from supabase import create_client
from dotenv import load_dotenv

# .envファイルを読み込む
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

# Supabase接続（SERVICE ROLE KEYを使用）
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_SERVICE_KEY:
    print("警告: SUPABASE_SERVICE_ROLE_KEY が設定されていません。通常のKEYを使用します。")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_KEY")

db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

print("=" * 80)
print("Rawdata_NETSUPER_items テーブルのトリガーとFunction定義を確認")
print("=" * 80)

# PostgreSQLのシステムカタログを直接クエリ
queries = [
    # トリガー一覧
    """
    SELECT
        trigger_name,
        event_manipulation,
        event_object_table,
        action_statement
    FROM information_schema.triggers
    WHERE event_object_table = 'Rawdata_NETSUPER_items'
    ORDER BY trigger_name;
    """,

    # Function定義を取得
    """
    SELECT
        p.proname as function_name,
        pg_get_functiondef(p.oid) as function_definition
    FROM pg_proc p
    JOIN pg_namespace n ON p.pronamespace = n.oid
    WHERE n.nspname = 'public'
        AND (
            p.proname LIKE '%embedding%'
            OR p.proname LIKE '%product%'
            OR pg_get_functiondef(p.oid) LIKE '%Rawdata_NETSUPER_items%'
        )
    ORDER BY p.proname;
    """
]

try:
    print("\n[1] Rawdata_NETSUPER_items のトリガー一覧:")
    print("-" * 80)
    result = db.rpc('exec_sql', {'query': queries[0]}).execute()
    if hasattr(result, 'data') and result.data:
        for row in result.data:
            print(f"トリガー名: {row.get('trigger_name')}")
            print(f"  イベント: {row.get('event_manipulation')}")
            print(f"  アクション: {row.get('action_statement')}")
            print()
    else:
        # RPCが使えない場合は直接情報を取得できないので、別の方法を試す
        print("RPC経由でのクエリができませんでした。")
        print("Supabaseダッシュボードで以下を確認してください:")
        print("Database > Functions / Triggers")

except Exception as e:
    print(f"エラー: {e}")
    print("\n代替方法:")
    print("1. Supabaseダッシュボード → Database → Triggers")
    print("2. SQL Editorで以下を実行:")
    print(queries[0])

print("\n" + "=" * 80)
print("推測: embeddingはデータベーストリガーで自動生成されている可能性")
print("=" * 80)
