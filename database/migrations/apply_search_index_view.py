#!/usr/bin/env python3
"""
DB参照ズレ解消スクリプト - VIEW と FUNCTION を作成
"""
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
client = create_client(url, key)

# 生成するSQL
VIEW_SQL = """
-- 互換VIEW: search_index
-- 実体テーブル 10_ix_search_index を search_index として参照可能にする
CREATE OR REPLACE VIEW public.search_index AS
SELECT
    document_id AS doc_id,
    chunk_content AS chunk_text,
    embedding,
    chunk_index,
    id AS chunk_id,
    chunk_type,
    search_weight,
    created_at
FROM public."10_ix_search_index";
"""

FUNCTION_SQL = """
-- 検索関数: match_documents
-- search_index VIEW を使ってベクトル類似検索を行う
CREATE OR REPLACE FUNCTION public.match_documents(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    doc_id uuid,
    chunk_text text,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.doc_id,
        s.chunk_text,
        1 - (s.embedding <=> query_embedding) AS similarity
    FROM public.search_index s
    WHERE s.embedding IS NOT NULL
      AND 1 - (s.embedding <=> query_embedding) > match_threshold
    ORDER BY s.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
"""

GRANT_SQL = """
-- 権限付与
GRANT SELECT ON public.search_index TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.match_documents(vector(1536), float, int) TO anon, authenticated;
"""

print("=" * 60)
print("DB参照ズレ解消: VIEW と FUNCTION を作成")
print("=" * 60)

# exec_sql RPC関数を試す
print("\n[Step 1] exec_sql RPC関数で実行を試みます...")

try:
    # VIEW作成
    print("VIEWを作成中...")
    result = client.rpc('exec_sql', {'sql': VIEW_SQL}).execute()
    print(f"VIEW作成: {result}")
except Exception as e:
    print(f"exec_sql 失敗: {e}")
    print("\n" + "=" * 60)
    print("exec_sql RPC関数が存在しないか、権限がありません。")
    print("以下のSQLをSupabase SQL Editorで手動実行してください:")
    print("=" * 60)
    print("\n-- 1. VIEWの作成")
    print(VIEW_SQL)
    print("\n-- 2. 検索関数の作成")
    print(FUNCTION_SQL)
    print("\n-- 3. 権限付与")
    print(GRANT_SQL)
    print("\n" + "=" * 60)
    print("上記SQLをSupabase Dashboard > SQL Editor で実行してください")
    print("=" * 60)
    sys.exit(0)

# 成功した場合は続行
try:
    print("FUNCTIONを作成中...")
    result = client.rpc('exec_sql', {'sql': FUNCTION_SQL}).execute()
    print(f"FUNCTION作成: {result}")

    print("権限を付与中...")
    result = client.rpc('exec_sql', {'sql': GRANT_SQL}).execute()
    print(f"権限付与: {result}")

    print("\n✅ すべて成功しました！")

except Exception as e:
    print(f"エラー: {e}")
