-- ============================================================
-- match_documents 関数を修正
-- ============================================================
-- 旧バージョンは Rawdata_FILE_AND_MAIL.embedding を参照していたが、
-- embedding は 10_ix_search_index に格納されている。
-- また削除済みカラム (source_type, source_id) を参照していた。
-- ⚠️ この関数はフォールバック用。通常検索は unified_search_v2 を使用。
-- ============================================================

DROP FUNCTION IF EXISTS match_documents(vector(1536), float, int, text);
DROP FUNCTION IF EXISTS match_documents(vector(1536), float, int);

CREATE OR REPLACE FUNCTION match_documents(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.0,
    match_count     int   DEFAULT 10
)
RETURNS TABLE (
    document_id UUID,
    file_name   TEXT,
    doc_type    TEXT,
    workspace   TEXT,
    similarity  FLOAT,
    chunk_text  TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT ON (s.document_id)
        s.document_id,
        r.file_name::TEXT,
        r.doc_type::TEXT,
        r.workspace::TEXT,
        (1.0 - (s.embedding <=> query_embedding))::FLOAT AS similarity,
        s.chunk_content::TEXT AS chunk_text
    FROM "10_ix_search_index" s
    JOIN "Rawdata_FILE_AND_MAIL" r ON r.id = s.document_id
    WHERE
        s.embedding IS NOT NULL
        AND (1.0 - (s.embedding <=> query_embedding)) >= match_threshold
    ORDER BY s.document_id, (s.embedding <=> query_embedding)
    LIMIT match_count;
END;
$$;

REVOKE ALL ON FUNCTION match_documents FROM PUBLIC;
REVOKE ALL ON FUNCTION match_documents FROM anon;
GRANT EXECUTE ON FUNCTION match_documents TO service_role;
GRANT EXECUTE ON FUNCTION match_documents TO authenticated;

COMMENT ON FUNCTION match_documents IS
'フォールバック検索: 10_ix_search_index を使ったベクトル検索。通常は unified_search_v2 を使用。';

DO $$
BEGIN
    RAISE NOTICE '✅ 20260227000005_fix_match_documents.sql 適用完了';
    RAISE NOTICE '  - match_documents: 10_ix_search_index を参照するよう修正';
    RAISE NOTICE '  - 削除済みカラム (source_type, source_id) の参照を除去';
END $$;
