-- ============================================================
-- file_url カラム新設
-- ============================================================
--
-- 【背景】
-- source_url が Google Drive ファイル URL と取得元ページ URL を兼務していて混乱。
-- file_url（Drive ファイル URL）を新設し、既存の source_url 値をコピーする。
-- source_url はそのまま残し、将来的に取得元ページ URL を入れる用途にする。
--
-- 【前提】
-- 20260226000001_drop_legacy_source_columns.sql 適用済み
--   → Rawdata_FILE_AND_MAIL から source_id / source_type / display_type は既に削除済み
--   → dequeue_document / unified_search_v2 からも上記カラムは除去済み

-- ============================================================
-- 1. カラム追加
-- ============================================================

ALTER TABLE "Rawdata_FILE_AND_MAIL" ADD COLUMN IF NOT EXISTS file_url TEXT;

-- ============================================================
-- 2. 既存データを source_url からコピー
-- ============================================================

UPDATE "Rawdata_FILE_AND_MAIL"
SET file_url = source_url
WHERE source_url IS NOT NULL;

-- ============================================================
-- 3. dequeue_document を再定義: file_url を追加
--    （20260226000001 版をベースに file_url を追加）
-- ============================================================

DROP FUNCTION IF EXISTS dequeue_document(TEXT, INT, TEXT);

CREATE OR REPLACE FUNCTION dequeue_document(
    p_workspace TEXT DEFAULT 'all',
    p_lease_seconds INT DEFAULT 900,
    p_owner TEXT DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    file_name TEXT,
    title TEXT,
    workspace TEXT,
    doc_type TEXT,
    processing_status TEXT,
    source_url TEXT,
    file_url TEXT,
    display_subject TEXT,
    display_post_text TEXT,
    attachment_text TEXT,
    display_sender TEXT,
    display_sender_email TEXT,
    display_sent_at TIMESTAMPTZ,
    screenshot_url TEXT,
    owner_id UUID,
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    attempt_count INT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[dequeue_document] p_owner is required.';
    END IF;

    RETURN QUERY
    UPDATE "Rawdata_FILE_AND_MAIL" AS t
    SET
        processing_status = 'processing',
        lease_owner = p_owner,
        lease_until = now() + make_interval(secs => p_lease_seconds),
        attempt_count = COALESCE(t.attempt_count, 0) + 1,
        updated_at = now()
    WHERE t.id = (
        SELECT r.id
        FROM "Rawdata_FILE_AND_MAIL" r
        WHERE
            (
                r.processing_status = 'queued'
                OR (r.processing_status = 'processing' AND r.lease_until < now())
            )
            AND (p_workspace = 'all' OR r.workspace = p_workspace)
            AND COALESCE(r.attempt_count, 0) < 5
        ORDER BY r.created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING
        t.id,
        t.file_name::TEXT,
        t.title::TEXT,
        t.workspace::TEXT,
        t.doc_type::TEXT,
        t.processing_status::TEXT,
        t.source_url::TEXT,
        t.file_url::TEXT,
        t.display_subject::TEXT,
        t.display_post_text::TEXT,
        t.attachment_text::TEXT,
        t.display_sender::TEXT,
        t.display_sender_email::TEXT,
        t.display_sent_at,
        t.screenshot_url::TEXT,
        t.owner_id,
        t.lease_owner::TEXT,
        t.lease_until,
        t.attempt_count;
END;
$$;

REVOKE ALL ON FUNCTION dequeue_document(TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dequeue_document(TEXT, INT, TEXT) TO service_role;

COMMENT ON FUNCTION dequeue_document IS
'リース方式デキュー v6: file_url を追加。source_url も後方互換で残す。';

-- ============================================================
-- 4. unified_search_v2 を再定義: file_url を追加
--    （20260226000001 版をベースに file_url を追加）
-- ============================================================

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT oid, pg_get_function_identity_arguments(oid) AS args
        FROM pg_proc
        WHERE proname = 'unified_search_v2'
    LOOP
        EXECUTE 'DROP FUNCTION IF EXISTS unified_search_v2(' || r.args || ')';
    END LOOP;
END $$;

CREATE FUNCTION unified_search_v2(
    query_text       TEXT,
    query_embedding  vector(1536),
    match_threshold  FLOAT   DEFAULT 0.0,
    match_count      INT     DEFAULT 10,
    vector_weight    FLOAT   DEFAULT 0.7,
    fulltext_weight  FLOAT   DEFAULT 0.3,
    filter_doc_types TEXT[]  DEFAULT NULL,
    filter_chunk_types TEXT[] DEFAULT NULL,
    filter_workspace TEXT    DEFAULT NULL
)
RETURNS TABLE (
    document_id           UUID,
    file_name             TEXT,
    doc_type              TEXT,
    workspace             TEXT,
    document_date         DATE,
    metadata              JSONB,
    summary               TEXT,
    attachment_text       TEXT,
    best_chunk_text       TEXT,
    best_chunk_id         UUID,
    best_chunk_index      INT,
    best_chunk_type       TEXT,
    combined_score        FLOAT,
    raw_similarity        FLOAT,
    weighted_similarity   FLOAT,
    fulltext_score        FLOAT,
    title_matched         BOOLEAN,
    source_url            TEXT,
    file_url              TEXT,
    created_at            TIMESTAMPTZ,
    display_subject       TEXT,
    display_sender        TEXT,
    classroom_sender_email TEXT,
    display_sent_at       TIMESTAMPTZ,
    display_post_text     TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    WITH chunk_scores AS (
        SELECT
            s.id                                                        AS chunk_id,
            s.document_id,
            s.chunk_index,
            s.chunk_type,
            s.chunk_content                                             AS chunk_text,
            (1.0 - (s.embedding <=> query_embedding))::FLOAT            AS raw_sim,
            0.0::FLOAT                                                   AS ft_score
        FROM "10_ix_search_index" s
        WHERE
            (filter_chunk_types IS NULL OR s.chunk_type = ANY(filter_chunk_types))
            AND (1.0 - (s.embedding <=> query_embedding)) >= match_threshold
    ),
    weighted AS (
        SELECT
            cs.*,
            (vector_weight * cs.raw_sim + fulltext_weight * cs.ft_score)::FLOAT AS combined,
            (cs.chunk_type = 'title')::BOOLEAN                                  AS is_title
        FROM chunk_scores cs
    ),
    best_per_doc AS (
        SELECT DISTINCT ON (w.document_id)
            w.document_id,
            w.chunk_id      AS best_chunk_id,
            w.chunk_index   AS best_chunk_index,
            w.chunk_type    AS best_chunk_type,
            w.chunk_text    AS best_chunk_text,
            w.raw_sim       AS raw_similarity,
            w.combined      AS weighted_similarity,
            w.ft_score      AS fulltext_score,
            w.combined      AS combined_score,
            w.is_title      AS title_matched
        FROM weighted w
        ORDER BY w.document_id, w.combined DESC
    )
    SELECT
        bp.document_id,
        r.file_name::TEXT,
        r.doc_type::TEXT,
        r.workspace::TEXT,
        r.display_sent_at::DATE                        AS document_date,
        r.metadata,
        COALESCE(
            r.display_subject,
            (SELECT art->>'title'
             FROM jsonb_array_elements(
                COALESCE((r.g21_articles #>> '{}')::jsonb, '[]'::jsonb)) art
             WHERE art->>'title' IS NOT NULL AND art->>'title' != ''
             LIMIT 1)
        )::TEXT                                        AS summary,
        COALESCE(r.display_post_text, '')
        || COALESCE(
            (SELECT E'\n' || string_agg(
                COALESCE(t->>'description', ''),
                E'\n')
             FROM jsonb_array_elements(
                COALESCE((r.g12_table_analyses #>> '{}')::jsonb, '[]'::jsonb)) t
             WHERE t->>'description' IS NOT NULL),
            '')
        || COALESCE(
            (SELECT E'\n' || string_agg(art->>'body', E'\n')
             FROM jsonb_array_elements(
                COALESCE((r.g21_articles #>> '{}')::jsonb, '[]'::jsonb)) art
             WHERE art->>'body' IS NOT NULL AND art->>'body' != ''),
            '')
        || COALESCE(
            (SELECT E'\n' || string_agg(
                COALESCE(ev->>'event', '') || ' ' || COALESCE(ev->>'date', ''),
                E'\n')
             FROM jsonb_array_elements(
                COALESCE(
                    (r.g22_ai_extracted #>> '{}')::jsonb -> 'calendar_events',
                    '[]'::jsonb)) ev
             WHERE ev->>'event' IS NOT NULL),
            '')
        || COALESCE(
            (SELECT E'\n' || string_agg(n->>'content', E'\n')
             FROM jsonb_array_elements(
                COALESCE(
                    (r.g22_ai_extracted #>> '{}')::jsonb -> 'notices',
                    '[]'::jsonb)) n
             WHERE n->>'content' IS NOT NULL),
            '')                                        AS attachment_text,
        bp.best_chunk_text::TEXT,
        bp.best_chunk_id,
        bp.best_chunk_index,
        bp.best_chunk_type::TEXT,
        bp.combined_score,
        bp.raw_similarity,
        bp.weighted_similarity,
        bp.fulltext_score,
        bp.title_matched,
        r.source_url::TEXT,
        r.file_url::TEXT,
        r.created_at,
        r.display_subject::TEXT,
        r.display_sender::TEXT,
        r.display_sender_email::TEXT                  AS classroom_sender_email,
        r.display_sent_at,
        r.display_post_text::TEXT
    FROM best_per_doc bp
    JOIN "Rawdata_FILE_AND_MAIL" r ON r.id = bp.document_id
    WHERE
        (filter_doc_types IS NULL OR r.doc_type = ANY(filter_doc_types))
        AND (filter_workspace IS NULL OR r.workspace = filter_workspace)
    ORDER BY bp.combined_score DESC
    LIMIT match_count;
END;
$$;

REVOKE ALL ON FUNCTION unified_search_v2 FROM PUBLIC;
REVOKE ALL ON FUNCTION unified_search_v2 FROM anon;
GRANT EXECUTE ON FUNCTION unified_search_v2 TO service_role;
GRANT EXECUTE ON FUNCTION unified_search_v2 TO authenticated;

COMMENT ON FUNCTION unified_search_v2 IS
'ハイブリッド検索（ベクトル検索のみ、ft_score=0固定）。'
'file_url を追加（v6）。source_url も後方互換で残す。';

-- ============================================================
-- 完了ログ
-- ============================================================

DO $$
BEGIN
    RAISE NOTICE '✅ 20260226000002_add_file_url.sql 適用完了';
    RAISE NOTICE '  - Rawdata_FILE_AND_MAIL: file_url カラム追加';
    RAISE NOTICE '  - 既存 source_url → file_url にコピー';
    RAISE NOTICE '  - dequeue_document: file_url を追加（v6）';
    RAISE NOTICE '  - unified_search_v2: file_url を追加（v6）';
END $$;
