-- unified_search_v2 v14: Googleカレンダーは calendar_* の狭い範囲のみ。他ソースは filter_* の広い窓。

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
    query_text         TEXT,
    query_embedding    vector(1536),
    match_threshold    FLOAT    DEFAULT 0.0,
    match_count        INT      DEFAULT 10,
    vector_weight      FLOAT    DEFAULT 0.7,
    fulltext_weight    FLOAT    DEFAULT 0.3,
    filter_sources     TEXT[]   DEFAULT NULL,
    filter_chunk_types TEXT[]   DEFAULT NULL,
    filter_persons     TEXT[]   DEFAULT NULL,
    filter_category    TEXT[]   DEFAULT NULL,
    filter_date_start  DATE     DEFAULT NULL,
    filter_date_end    DATE     DEFAULT NULL,
    calendar_filter_date_start DATE DEFAULT NULL,
    calendar_filter_date_end   DATE DEFAULT NULL
)
RETURNS TABLE (
    doc_id              UUID,
    person              TEXT,
    source              TEXT,
    category            TEXT,
    title               TEXT,
    from_name           TEXT,
    from_email          TEXT,
    snippet             TEXT,
    post_at             TIMESTAMPTZ,
    start_at            TIMESTAMPTZ,
    end_at              TIMESTAMPTZ,
    due_date            DATE,
    location            TEXT,
    file_url            TEXT,
    ui_data             JSONB,
    meta                JSONB,
    ix_date_signals     JSONB,
    ix_search_dates     DATE[],
    indexed_at          TIMESTAMPTZ,
    best_chunk_text     TEXT,
    best_chunk_id       UUID,
    best_chunk_index    INT,
    best_chunk_type     TEXT,
    combined_score      FLOAT,
    raw_similarity      FLOAT,
    weighted_similarity FLOAT,
    fulltext_score      FLOAT,
    title_matched       BOOLEAN
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
            s.doc_id,
            s.chunk_index,
            s.chunk_type,
            s.chunk_text,
            s.chunk_weight,
            (1.0 - (s.embedding <=> query_embedding))::FLOAT            AS raw_sim,
            0.0::FLOAT                                                   AS ft_score
        FROM "10_ix_search_index" s
        INNER JOIN "09_unified_documents" ud ON ud.id = s.doc_id
        WHERE
            (filter_chunk_types IS NULL OR s.chunk_type = ANY(filter_chunk_types))
            AND (filter_sources  IS NULL OR s.source    = ANY(filter_sources))
            AND (filter_persons  IS NULL OR s.person    = ANY(filter_persons))
            AND (filter_category IS NULL OR ud.category = ANY(filter_category))
            AND (
                filter_date_start IS NULL OR filter_date_end IS NULL
                OR (
                    CASE
                        WHEN ud.source = 'Googleカレンダー'
                             AND calendar_filter_date_start IS NOT NULL
                             AND calendar_filter_date_end IS NOT NULL
                        THEN EXISTS (
                            SELECT 1
                            FROM unnest(COALESCE(ud.ix_search_dates, ARRAY[]::DATE[])) AS d
                            WHERE d BETWEEN calendar_filter_date_start AND calendar_filter_date_end
                        )
                        ELSE EXISTS (
                            SELECT 1
                            FROM unnest(COALESCE(ud.ix_search_dates, ARRAY[]::DATE[])) AS d
                            WHERE d BETWEEN filter_date_start AND filter_date_end
                        )
                    END
                )
            )
            AND (1.0 - (s.embedding <=> query_embedding)) >= match_threshold
    ),
    weighted AS (
        SELECT
            cs.*,
            ((vector_weight * cs.raw_sim + fulltext_weight * cs.ft_score)
             * cs.chunk_weight)::FLOAT                                   AS combined,
            (cs.chunk_type = 'title')::BOOLEAN                          AS is_title
        FROM chunk_scores cs
    ),
    best_per_doc AS (
        SELECT DISTINCT ON (w.doc_id)
            w.doc_id,
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
        ORDER BY w.doc_id, w.combined DESC
    )
    SELECT
        ud.id                              AS doc_id,
        ud.person,
        ud.source,
        ud.category,
        ud.title,
        ud.from_name,
        ud.from_email,
        ud.snippet,
        ud.post_at,
        ud.start_at,
        ud.end_at,
        ud.due_date,
        ud.location,
        ud.file_url,
        ud.ui_data,
        ud.meta,
        ud.ix_date_signals,
        ud.ix_search_dates,
        ud.indexed_at,
        bp.best_chunk_text::TEXT,
        bp.best_chunk_id,
        bp.best_chunk_index,
        bp.best_chunk_type::TEXT,
        bp.combined_score,
        bp.raw_similarity,
        bp.weighted_similarity,
        bp.fulltext_score,
        bp.title_matched
    FROM best_per_doc bp
    JOIN "09_unified_documents" ud ON ud.id = bp.doc_id
    ORDER BY bp.combined_score DESC
    LIMIT match_count;
END;
$$;

REVOKE ALL ON FUNCTION unified_search_v2 FROM PUBLIC;
REVOKE ALL ON FUNCTION unified_search_v2 FROM anon;
GRANT EXECUTE ON FUNCTION unified_search_v2 TO service_role;
GRANT EXECUTE ON FUNCTION unified_search_v2 TO authenticated;

COMMENT ON FUNCTION unified_search_v2 IS
'ハイブリッド検索（v14）: カレンダーは calendar_* の狭い範囲、他は filter_* の広い範囲で ix_search_dates を判定。';
