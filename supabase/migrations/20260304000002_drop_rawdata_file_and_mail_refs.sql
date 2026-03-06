-- ============================================================
-- Rawdata_FILE_AND_MAIL 参照の完全削除
--
-- 背景:
--   20260302000008 で waseda_academy データを pipeline_meta +
--   05_ikuya_waseaca_01_raw に移行済み。
--   Rawdata_FILE_AND_MAIL テーブルはもう存在しないが、
--   古い関数・ビューがまだ参照しているため
--   pipeline_meta 操作時に 42P01 エラーが発生する。
--
-- 削除対象:
--   関数: clear_queue, enqueue_documents, get_queue_status, dequeue_document
--   ビュー: v_ops_summary_24h, v_failed_reasons_7d, v_retry_dead, v_skip_summary
--
-- 代替:
--   Python コードは pipeline_meta を直接 UPDATE するため RPC 不要
--   ビューは pipeline_meta ベースで再作成
-- ============================================================

-- ============================================================
-- 1. 旧キュー管理 RPC を pipeline_meta ベースに置き換え
-- ============================================================

-- 1-1. clear_queue: pipeline_meta.pending → failed
--   シグネチャ維持（返り値 cleared_count）
DROP FUNCTION IF EXISTS clear_queue(TEXT);

CREATE OR REPLACE FUNCTION clear_queue(
    p_workspace TEXT DEFAULT 'all'
)
RETURNS TABLE (
    cleared_count INT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INT;
BEGIN
    UPDATE public.pipeline_meta
    SET
        processing_status = 'failed',
        last_error_reason = 'キューから手動除外（clear_queue）',
        updated_at        = now()
    WHERE
        processing_status = 'pending'
        AND (p_workspace = 'all' OR source = p_workspace);

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN QUERY SELECT v_count;
END;
$$;

REVOKE ALL ON FUNCTION clear_queue(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION clear_queue(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION clear_queue(TEXT) TO authenticated;

COMMENT ON FUNCTION clear_queue IS
'pipeline_meta ベース（Rawdata_FILE_AND_MAIL 廃止後）: pending → failed に移動。元の clear_queue シグネチャを維持。';

-- 1-2. get_queue_status: pipeline_meta ベースで再作成
DROP FUNCTION IF EXISTS get_queue_status(TEXT);

CREATE OR REPLACE FUNCTION get_queue_status(
    p_workspace TEXT DEFAULT 'all'
)
RETURNS TABLE (
    pending_count   BIGINT,
    queued_count    BIGINT,
    processing_count BIGINT,
    completed_count BIGINT,
    failed_count    BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*) FILTER (WHERE processing_status = 'pending'),
        0::BIGINT,  -- queued は廃止（常に0）
        COUNT(*) FILTER (WHERE processing_status = 'processing'),
        COUNT(*) FILTER (WHERE processing_status = 'completed'),
        COUNT(*) FILTER (WHERE processing_status = 'failed')
    FROM public.pipeline_meta
    WHERE p_workspace = 'all' OR source = p_workspace;
END;
$$;

REVOKE ALL ON FUNCTION get_queue_status(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION get_queue_status(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION get_queue_status(TEXT) TO authenticated;

-- 1-3. 旧 dequeue_document / enqueue_documents / ack_document / nack_document / renew_lease を削除
--   これらは pipeline_meta の dequeue_pipeline / ack_pipeline / nack_pipeline / renew_pipeline_lease に完全移行済み
DROP FUNCTION IF EXISTS dequeue_document(TEXT, INT, TEXT);
DROP FUNCTION IF EXISTS enqueue_documents(TEXT, INT, UUID[]);
DROP FUNCTION IF EXISTS ack_document(UUID, TEXT);
DROP FUNCTION IF EXISTS nack_document(UUID, TEXT, TEXT, BOOLEAN);
DROP FUNCTION IF EXISTS renew_lease(UUID, TEXT, INT);

-- ============================================================
-- 2. 旧ビューを削除（全て Rawdata_FILE_AND_MAIL 参照）
-- ============================================================

DROP VIEW IF EXISTS public.v_ops_summary_24h;
DROP VIEW IF EXISTS public.v_failed_reasons_7d;
DROP VIEW IF EXISTS public.v_retry_dead;
DROP VIEW IF EXISTS public.v_skip_summary;

-- ============================================================
-- 3. ビューを pipeline_meta ベースで再作成
-- ============================================================

-- 3-1. 運用サマリ VIEW（24時間）
CREATE OR REPLACE VIEW public.v_ops_summary_24h AS
WITH r AS (
  SELECT processing_status
  FROM public.pipeline_meta
  WHERE created_at >= now() - interval '24 hours'
)
SELECT
  now() AS as_of,
  count(*) AS rawdata_24h_total,
  count(*) FILTER (WHERE processing_status = 'pending')    AS pending,
  count(*) FILTER (WHERE processing_status = 'processing') AS processing,
  count(*) FILTER (WHERE processing_status = 'completed')  AS completed,
  count(*) FILTER (WHERE processing_status = 'failed')     AS failed
FROM r;

-- 3-2. 失敗原因 TOP VIEW（7日）
CREATE OR REPLACE VIEW public.v_failed_reasons_7d AS
WITH f AS (
  SELECT
    last_error_reason AS err200,
    failed_at
  FROM public.pipeline_meta
  WHERE processing_status = 'failed'
    AND failed_at >= now() - interval '7 days'
)
SELECT
  CASE
    WHEN err200 IS NULL OR err200 = '' THEN '(no_error)'
    ELSE left(err200, 200)
  END AS error_sample,
  count(*) AS cnt,
  max(failed_at) AS last_seen
FROM f
GROUP BY 1;

-- ============================================================
-- 4. 旧インデックスを削除（Rawdata_FILE_AND_MAIL 参照）
-- ============================================================

DROP INDEX IF EXISTS public.idx_rawdata_queued;
DROP INDEX IF EXISTS public.ix_rawdata_status_failedat;
DROP INDEX IF EXISTS public.ix_rawdata_status_createdat;
DROP INDEX IF EXISTS public.ix_rawdata_skipcode_skippedat;

-- ============================================================
-- 5. 権限付与
-- ============================================================

GRANT SELECT ON public.v_ops_summary_24h TO anon, authenticated;
GRANT SELECT ON public.v_failed_reasons_7d TO anon, authenticated;

-- ============================================================
-- 完了確認
-- ============================================================

DO $$
BEGIN
  RAISE NOTICE '====================================================';
  RAISE NOTICE '✅ 20260304000002 適用完了';
  RAISE NOTICE '  削除: clear_queue, enqueue_documents, get_queue_status, dequeue_document';
  RAISE NOTICE '  削除: ack_document, nack_document, renew_lease';
  RAISE NOTICE '  削除: v_ops_summary_24h, v_failed_reasons_7d, v_retry_dead, v_skip_summary';
  RAISE NOTICE '  再作成: v_ops_summary_24h, v_failed_reasons_7d (pipeline_meta ベース)';
  RAISE NOTICE '====================================================';
END $$;
