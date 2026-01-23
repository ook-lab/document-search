-- ============================================================
-- DB参照ズレ解消: search_index 互換VIEW + match_documents 関数
--
-- 実行方法:
--   1. Supabase Dashboard (https://supabase.com/dashboard) にログイン
--   2. プロジェクト選択 > SQL Editor
--   3. このファイルの内容を全てコピー&ペースト
--   4. 「Run」ボタンをクリック
-- ============================================================

-- ============================================================
-- STEP 1: 互換VIEW search_index の作成
-- 実体テーブル: 10_ix_search_index
-- 目的: アプリ側の search_index 参照を壊さず互換性を維持
-- ============================================================
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

-- ============================================================
-- STEP 2: 検索関数 match_documents の作成
-- ベクトル類似検索を行い、閾値以上の結果を返す
-- ============================================================
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
        (1 - (s.embedding <=> query_embedding))::float AS similarity
    FROM public.search_index s
    WHERE s.embedding IS NOT NULL
      AND (1 - (s.embedding <=> query_embedding)) > match_threshold
    ORDER BY s.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ============================================================
-- STEP 3: 権限付与
-- anon（匿名ユーザー）と authenticated（認証済みユーザー）に
-- VIEWの参照権限と関数の実行権限を付与
-- ============================================================
GRANT SELECT ON public.search_index TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.match_documents(vector(1536), float, int) TO anon, authenticated;

-- ============================================================
-- STEP 4: 検証クエリ（実行後に確認）
-- ============================================================
-- VIEWの件数確認
SELECT 'search_index件数' AS check_item, count(*) AS result FROM public.search_index;

-- VIEWのサンプルデータ
SELECT 'search_indexサンプル' AS check_item, doc_id, left(chunk_text, 50) AS chunk_preview
FROM public.search_index LIMIT 3;

-- 関数の存在確認
SELECT 'match_documents関数' AS check_item, proname AS result
FROM pg_proc
JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace
WHERE nspname = 'public' AND proname = 'match_documents';
