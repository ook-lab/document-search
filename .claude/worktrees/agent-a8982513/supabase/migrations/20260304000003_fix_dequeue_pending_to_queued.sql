-- ============================================================
-- dequeue_pipeline: pending → queued を処理対象に変更
-- pending = 取り込み済み・未キュー
-- queued  = 明示的にキューに追加済み（処理対象）
-- ============================================================

-- インデックス再作成
DROP INDEX IF EXISTS idx_pm_queue_lease;
CREATE INDEX idx_pm_queue_lease
  ON public.pipeline_meta (processing_status, lease_until)
  WHERE processing_status IN ('queued', 'processing');

-- RPC 再作成
DROP FUNCTION IF EXISTS dequeue_pipeline(TEXT, INT, TEXT);

CREATE OR REPLACE FUNCTION dequeue_pipeline(
    p_raw_table     TEXT,
    p_lease_seconds INT  DEFAULT 900,
    p_owner         TEXT DEFAULT NULL
)
RETURNS TABLE (
    meta_id       UUID,
    raw_id        UUID,
    raw_table     TEXT,
    person        TEXT,
    source        TEXT,
    attempt_count INT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[dequeue_pipeline] p_owner is required.';
    END IF;

    RETURN QUERY
    UPDATE public.pipeline_meta AS t
    SET
        processing_status = 'processing',
        lease_owner       = p_owner,
        lease_until       = now() + make_interval(secs => p_lease_seconds),
        attempt_count     = COALESCE(t.attempt_count, 0) + 1,
        updated_at        = now(),
        started_at        = COALESCE(t.started_at, now())
    WHERE t.id = (
        SELECT m.id
        FROM public.pipeline_meta m
        WHERE
            (
                m.processing_status = 'queued'
                OR (m.processing_status = 'processing' AND m.lease_until < now())
            )
            AND m.raw_table = p_raw_table
        ORDER BY m.created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING
        t.id,
        t.raw_id,
        t.raw_table,
        t.person,
        t.source,
        t.attempt_count;
END;
$$;

REVOKE ALL ON FUNCTION dequeue_pipeline(TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dequeue_pipeline(TEXT, INT, TEXT) TO service_role;

COMMENT ON FUNCTION dequeue_pipeline IS
'pipeline_meta リース方式デキュー: queued を処理対象として1件取得し processing + リース設定を原子化';

DO $$
BEGIN
  RAISE NOTICE 'dequeue_pipeline: pending → queued に変更完了';
END $$;
