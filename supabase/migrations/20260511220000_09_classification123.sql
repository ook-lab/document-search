-- 09_unified_documents / 10_ix_search_index: source・category を classification1〜3 に再編
-- 1=旧source 2=コース名（主にClassroom）3=旧category。既存行は 2 を NULL でコピー。

ALTER TABLE public."09_unified_documents"
  ADD COLUMN IF NOT EXISTS classification1 TEXT,
  ADD COLUMN IF NOT EXISTS classification2 TEXT,
  ADD COLUMN IF NOT EXISTS classification3 TEXT;

UPDATE public."09_unified_documents"
SET
  classification1 = source,
  classification2 = NULL,
  classification3 = category;

ALTER TABLE public."09_unified_documents" DROP COLUMN IF EXISTS source;
ALTER TABLE public."09_unified_documents" DROP COLUMN IF EXISTS category;

DROP INDEX IF EXISTS idx_09_unified_source;
DROP INDEX IF EXISTS idx_09_unified_category;
CREATE INDEX IF NOT EXISTS idx_09_unified_classification1 ON public."09_unified_documents" (classification1);
CREATE INDEX IF NOT EXISTS idx_09_unified_classification2 ON public."09_unified_documents" (classification2);
CREATE INDEX IF NOT EXISTS idx_09_unified_classification3 ON public."09_unified_documents" (classification3);

-- 10_ix
ALTER TABLE public."10_ix_search_index"
  ADD COLUMN IF NOT EXISTS classification1 TEXT,
  ADD COLUMN IF NOT EXISTS classification2 TEXT,
  ADD COLUMN IF NOT EXISTS classification3 TEXT;

UPDATE public."10_ix_search_index"
SET
  classification1 = source,
  classification2 = NULL,
  classification3 = category;

ALTER TABLE public."10_ix_search_index" DROP COLUMN IF EXISTS source;
ALTER TABLE public."10_ix_search_index" DROP COLUMN IF EXISTS category;

DROP INDEX IF EXISTS idx_10_ix_source;
DROP INDEX IF EXISTS idx_10_ix_category;
CREATE INDEX IF NOT EXISTS idx_10_ix_classification1 ON public."10_ix_search_index" (classification1);
CREATE INDEX IF NOT EXISTS idx_10_ix_classification2 ON public."10_ix_search_index" (classification2);
CREATE INDEX IF NOT EXISTS idx_10_ix_classification3 ON public."10_ix_search_index" (classification3);

DROP TRIGGER IF EXISTS trg_sync_10_from_09 ON public."09_unified_documents";

CREATE OR REPLACE FUNCTION public.sync_10_from_09()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE public."10_ix_search_index"
  SET
    person           = NEW.person,
    classification1  = NEW.classification1,
    classification2  = NEW.classification2,
    classification3  = NEW.classification3
  WHERE doc_id = NEW.id;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_sync_10_from_09
  AFTER UPDATE OF person, classification1, classification2, classification3
  ON public."09_unified_documents"
  FOR EACH ROW
  EXECUTE FUNCTION public.sync_10_from_09();

-- unified_search_v2 v16
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
    classification1     TEXT,
    classification2     TEXT,
    classification3     TEXT,
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
            (ud.classification1 IS DISTINCT FROM 'Googleカレンダー')
            AND (filter_chunk_types IS NULL OR s.chunk_type = ANY(filter_chunk_types))
            AND (filter_sources  IS NULL OR s.classification1 = ANY(filter_sources))
            AND (filter_persons  IS NULL OR s.person    = ANY(filter_persons))
            AND (filter_category IS NULL OR ud.classification3 = ANY(filter_category))
            AND (
                filter_date_start IS NULL OR filter_date_end IS NULL
                OR EXISTS (
                    SELECT 1
                    FROM unnest(COALESCE(ud.ix_search_dates, ARRAY[]::DATE[])) AS d
                    WHERE d BETWEEN filter_date_start AND filter_date_end
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
        ud.classification1,
        ud.classification2,
        ud.classification3,
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
'ハイブリッド検索（v16）: 09/10 の source,category を classification1,3 に変更。filter_* 引数名は互換のまま。';
