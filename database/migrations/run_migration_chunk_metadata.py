"""
chunk_metadata カラムを追加するマイグレーション

Supabase は直接 SQL を実行できないため、Postgrest API 経由でスキーマ変更を試みる
ただし、DDL (ALTER TABLE) は通常 Supabase Dashboard の SQL Editor で実行する必要がある
"""
import os
from pathlib import Path
import sys

# ユーザーに案内を表示
print("=" * 80)
print("⚠️  chunk_metadata カラム追加マイグレーション")
print("=" * 80)
print()
print("Supabase では、テーブルスキーマの変更（ALTER TABLE）は")
print("Supabase Dashboard の SQL Editor から直接実行する必要があります。")
print()
print("以下の手順に従ってください：")
print()
print("1. Supabase Dashboard にログイン")
print("   https://supabase.com/dashboard")
print()
print("2. 対象のプロジェクトを選択")
print()
print("3. 左側メニューから「SQL Editor」を選択")
print()
print("4. 以下のSQLを実行:")
print()
print("-" * 80)

# SQLファイルを読み込んで表示
sql_file = Path(__file__).parent / "add_chunk_metadata_column.sql"
if sql_file.exists():
    with open(sql_file, 'r') as f:
        sql_content = f.read()
    print(sql_content)
else:
    print("""
ALTER TABLE "10_ix_search_index"
ADD COLUMN IF NOT EXISTS chunk_metadata jsonb;

CREATE INDEX IF NOT EXISTS idx_chunk_metadata_gin
ON "10_ix_search_index" USING gin (chunk_metadata jsonb_path_ops);

COMMENT ON COLUMN "10_ix_search_index".chunk_metadata IS '構造化データ（text_blocks, structured_tables, weekly_schedule, other_text など）のメタデータ';
""")

print("-" * 80)
print()
print("5. 実行後、このスクリプトを再度実行して確認してください:")
print(f"   python3 {Path(__file__).name}")
print()
print("=" * 80)

# 確認モード
answer = input("\nマイグレーションを実行しましたか？ (yes/no): ")

if answer.lower() == 'yes':
    # スキーマを確認
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from shared.common.database.client import DatabaseClient

        db = DatabaseClient()
        response = db.client.table('10_ix_search_index').select('*').limit(1).execute()

        if response.data and 'chunk_metadata' in response.data[0]:
            print()
            print("✅ chunk_metadata カラムが正常に追加されました！")
            print()
        else:
            print()
            print("❌ chunk_metadata カラムがまだ存在しません。")
            print("   SQL Editor でマイグレーションを実行してください。")
            print()
    except Exception as e:
        print(f"\nERROR: スキーマ確認失敗: {e}\n")
else:
    print("\nマイグレーションをキャンセルしました。\n")
