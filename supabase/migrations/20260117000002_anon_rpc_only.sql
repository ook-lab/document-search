-- =============================================================================
-- Migration: Phase 4A - anon 公開面の最小化
-- =============================================================================
-- 目的: anon は実テーブルに直接アクセスせず、RPC 経由のみでデータ取得
-- =============================================================================

BEGIN;

-- =============================================================================
-- STEP 1: anon の実テーブル SELECT を REVOKE
-- =============================================================================

REVOKE SELECT ON "Rawdata_FILE_AND_MAIL" FROM anon;
REVOKE SELECT ON "10_ix_search_index" FROM anon;

-- =============================================================================
-- STEP 2: public_search RPC を作成（返却フィールド最小化）
-- =============================================================================
-- 返却フィールド:
--   - document_id: ドキュメントID
--   - file_name: ファイル名
--   - doc_type: ドキュメントタイプ
--   - workspace: ワークスペース
--   - document_date: ドキュメント日付
--   - summary: 要約（本文ではない）
--   - similarity: 類似度スコア
--   - chunk_preview: チャンクプレビュー（100文字以内）
--
-- 除外フィールド（PII/本文）:
--   - attachment_text（本文全体）
--   - display_sender_email（メールアドレス）
--   - display_post_text（投稿本文）
--   - metadata（詳細メタデータ）
--   - owner_id（所有者情報）
-- =============================================================================

CREATE OR REPLACE FUNCTION public_search(
    query_text TEXT,
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.0,
    match_count INT DEFAULT 10
)
RETURNS TABLE (
    document_id UUID,
    file_name TEXT,
    doc_type TEXT,
    workspace TEXT,
    document_date DATE,
    summary TEXT,
    similarity FLOAT,
    chunk_preview TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER  -- service_role 権限で実行（RLS バイパス）
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    WITH ranked_chunks AS (
        SELECT
            idx.document_id,
            idx.chunk_content,
            1 - (idx.embedding <=> query_embedding) AS vector_similarity,
            ROW_NUMBER() OVER (
                PARTITION BY idx.document_id
                ORDER BY 1 - (idx.embedding <=> query_embedding) DESC
            ) AS rank
        FROM "10_ix_search_index" idx
        WHERE 1 - (idx.embedding <=> query_embedding) > match_threshold
    ),
    best_chunks AS (
        SELECT *
        FROM ranked_chunks
        WHERE rank = 1
    )
    SELECT
        doc.id AS document_id,
        doc.file_name,
        doc.doc_type,
        doc.workspace,
        doc.document_date,
        -- summary は 200 文字以内に制限
        LEFT(COALESCE(doc.summary, ''), 200) AS summary,
        bc.vector_similarity AS similarity,
        -- chunk_preview は 100 文字以内に制限
        LEFT(bc.chunk_content, 100) AS chunk_preview
    FROM best_chunks bc
    JOIN "Rawdata_FILE_AND_MAIL" doc ON bc.document_id = doc.id
    ORDER BY bc.vector_similarity DESC
    LIMIT match_count;
END;
$$;

-- =============================================================================
-- STEP 3: public_search_with_fulltext RPC（ハイブリッド検索用）
-- =============================================================================

-- NOTE: filter_doc_types と filter_workspace は anon 公開 RPC では削除
-- 列挙攻撃（enumeration）を防ぐため、公開検索は全データ対象に固定
-- authenticated/service_role 向けには unified_search_v2 を使用
CREATE OR REPLACE FUNCTION public_search_with_fulltext(
    query_text TEXT,
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.0,
    match_count INT DEFAULT 10,
    vector_weight FLOAT DEFAULT 0.7,
    fulltext_weight FLOAT DEFAULT 0.3
)
RETURNS TABLE (
    document_id UUID,
    file_name TEXT,
    doc_type TEXT,
    workspace TEXT,
    document_date DATE,
    summary TEXT,
    combined_score FLOAT,
    chunk_preview TEXT,
    chunk_type TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    WITH scored_chunks AS (
        SELECT
            idx.document_id,
            idx.chunk_content,
            idx.chunk_type,
            -- ベクトル類似度
            1 - (idx.embedding <=> query_embedding) AS vector_similarity,
            -- 全文検索スコア
            COALESCE(
                ts_rank(
                    to_tsvector('japanese', idx.chunk_content),
                    plainto_tsquery('japanese', query_text)
                ),
                0
            ) AS fulltext_score
        FROM "10_ix_search_index" idx
        WHERE 1 - (idx.embedding <=> query_embedding) > match_threshold
    ),
    combined_scores AS (
        SELECT
            sc.*,
            (sc.vector_similarity * vector_weight + sc.fulltext_score * fulltext_weight) AS combined_score,
            ROW_NUMBER() OVER (
                PARTITION BY sc.document_id
                ORDER BY (sc.vector_similarity * vector_weight + sc.fulltext_score * fulltext_weight) DESC
            ) AS rank
        FROM scored_chunks sc
    ),
    best_chunks AS (
        SELECT *
        FROM combined_scores
        WHERE rank = 1
    )
    SELECT
        doc.id AS document_id,
        doc.file_name,
        doc.doc_type,
        doc.workspace,
        doc.document_date,
        LEFT(COALESCE(doc.summary, ''), 200) AS summary,
        bc.combined_score,
        LEFT(bc.chunk_content, 100) AS chunk_preview,
        bc.chunk_type
    FROM best_chunks bc
    JOIN "Rawdata_FILE_AND_MAIL" doc ON bc.document_id = doc.id
    -- NOTE: フィルタ引数は削除済み（列挙攻撃防止）
    ORDER BY bc.combined_score DESC
    LIMIT match_count;
END;
$$;

-- =============================================================================
-- STEP 4: RPC に anon からの EXECUTE を許可
-- =============================================================================

GRANT EXECUTE ON FUNCTION public_search(TEXT, vector(1536), FLOAT, INT) TO anon;
GRANT EXECUTE ON FUNCTION public_search_with_fulltext(TEXT, vector(1536), FLOAT, INT, FLOAT, FLOAT) TO anon;

-- authenticated も同様に許可（Admin UI 等で使用可能に）
GRANT EXECUTE ON FUNCTION public_search(TEXT, vector(1536), FLOAT, INT) TO authenticated;
GRANT EXECUTE ON FUNCTION public_search_with_fulltext(TEXT, vector(1536), FLOAT, INT, FLOAT, FLOAT) TO authenticated;

-- =============================================================================
-- STEP 5: 既存の unified_search_v2 が存在する場合は anon から REVOKE
-- =============================================================================
-- unified_search_v2 は内部詳細を多く返すため、anon には使用させない

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'unified_search_v2'
    ) THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION unified_search_v2 FROM anon';
        RAISE NOTICE 'unified_search_v2 の anon EXECUTE を REVOKE しました';
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'unified_search_v2 REVOKE スキップ: %', SQLERRM;
END $$;

COMMIT;

-- =============================================================================
-- 確認クエリ
-- =============================================================================
-- anon の権限を確認:
--
-- SELECT
--     grantee,
--     table_name,
--     privilege_type
-- FROM information_schema.table_privileges
-- WHERE table_schema = 'public'
-- AND grantee = 'anon';
--
-- RPC の権限を確認:
--
-- SELECT
--     proname,
--     proacl
-- FROM pg_proc
-- WHERE proname LIKE 'public_search%';
