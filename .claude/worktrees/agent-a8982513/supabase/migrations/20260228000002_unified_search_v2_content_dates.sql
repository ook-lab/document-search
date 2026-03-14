-- ============================================================
-- unified_search_v2 に content_dates を追加
-- ============================================================

-- 既存の全オーバーロードを削除
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
    content_dates         DATE[],
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
        r.content_dates,
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
                COALESCE((r.g17_table_analyses #>> '{}')::jsonb, '[]'::jsonb)) t
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
'ハイブリッド検索（v8）: content_dates DATE[] 追加。F3正規化済み全日付を返す。';

DO $$
BEGIN
    RAISE NOTICE '✅ 20260228000002_unified_search_v2_content_dates.sql 適用完了';
    RAISE NOTICE '  - unified_search_v2: content_dates DATE[] 列追加';
END $$;
