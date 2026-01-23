-- ============================================================
-- 運用ダッシュボード用SQL一式
--
-- 内容:
--   1. KPIサマリ（24時間）
--   2. 失敗原因TOP（7日）
--   3. completion_guard拒否一覧
--   4. スキップ理由TOP
--   5. retry_queue監視
--   6. 成果品質監視
--   7. VIEW化（ダッシュボード部品）
--   8. インデックス追加
--
-- 実行: Supabase SQL Editor で実行
-- ============================================================


-- ############################################################
-- PART 7: VIEW化（先に作成しておく）
-- ############################################################

-- 7-1. 運用サマリVIEW（24時間）
CREATE OR REPLACE VIEW public.v_ops_summary_24h AS
WITH r AS (
  SELECT *
  FROM public."Rawdata_FILE_AND_MAIL"
  WHERE created_at >= now() - interval '24 hours'
)
SELECT
  now() AS as_of,
  count(*) AS rawdata_24h_total,
  count(*) FILTER (WHERE processing_status = 'pending')    AS pending,
  count(*) FILTER (WHERE processing_status = 'processing') AS processing,
  count(*) FILTER (WHERE processing_status = 'completed')  AS completed,
  count(*) FILTER (WHERE processing_status = 'failed')     AS failed,
  count(*) FILTER (WHERE processing_status = 'skipped')    AS skipped,
  (SELECT count(*) FROM public.retry_queue WHERE status = 'queued') AS retry_queued,
  (SELECT count(*) FROM public.retry_queue WHERE status = 'leased') AS retry_leased,
  (SELECT count(*) FROM public.retry_queue WHERE status = 'dead')   AS retry_dead
FROM r;

-- 7-2. 失敗原因TOP VIEW（7日）
CREATE OR REPLACE VIEW public.v_failed_reasons_7d AS
WITH f AS (
  SELECT
    failed_stage,
    left(coalesce(error_message, ''), 200) AS err200,
    failed_at
  FROM public."Rawdata_FILE_AND_MAIL"
  WHERE processing_status = 'failed'
    AND failed_at >= now() - interval '7 days'
)
SELECT
  coalesce(failed_stage, '(no_stage)') AS failed_stage,
  CASE
    WHEN err200 = '' THEN '(no_error)'
    ELSE err200
  END AS error_sample,
  count(*) AS cnt,
  max(failed_at) AS last_seen
FROM f
GROUP BY 1, 2;

-- 7-3. retry_queue dead一覧 VIEW
CREATE OR REPLACE VIEW public.v_retry_dead AS
SELECT
  rq.rawdata_id,
  rq.retry_count,
  rq.updated_at,
  left(rq.last_error, 300) AS last_error,
  r.file_name,
  r.created_at,
  r.failed_at,
  r.failed_stage
FROM public.retry_queue rq
LEFT JOIN public."Rawdata_FILE_AND_MAIL" r ON r.id = rq.rawdata_id
WHERE rq.status = 'dead';

-- 7-4. スキップ理由サマリ VIEW
CREATE OR REPLACE VIEW public.v_skip_summary AS
SELECT
  skip_code,
  count(*) AS cnt,
  min(skipped_at) AS first_seen,
  max(skipped_at) AS last_seen
FROM public."Rawdata_FILE_AND_MAIL"
WHERE processing_status = 'skipped'
GROUP BY skip_code;


-- ############################################################
-- PART 8: インデックス追加（パフォーマンス最適化）
-- ############################################################

-- Rawdataの状態系フィルタに効く
CREATE INDEX IF NOT EXISTS ix_rawdata_status_failedat
ON public."Rawdata_FILE_AND_MAIL"(processing_status, failed_at DESC);

CREATE INDEX IF NOT EXISTS ix_rawdata_status_createdat
ON public."Rawdata_FILE_AND_MAIL"(processing_status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_rawdata_skipcode_skippedat
ON public."Rawdata_FILE_AND_MAIL"(skip_code, skipped_at DESC);

-- retry_queueの取り出し条件に効く
CREATE INDEX IF NOT EXISTS ix_retry_queue_ready
ON public.retry_queue(status, next_retry_at, leased_until);


-- ############################################################
-- 権限付与
-- ############################################################

GRANT SELECT ON public.v_ops_summary_24h TO anon, authenticated;
GRANT SELECT ON public.v_failed_reasons_7d TO anon, authenticated;
GRANT SELECT ON public.v_retry_dead TO anon, authenticated;
GRANT SELECT ON public.v_skip_summary TO anon, authenticated;


-- ############################################################
-- 検証: 作成されたVIEWとインデックスを確認
-- ############################################################

-- VIEW一覧
SELECT 'VIEW' AS type, table_name AS name
FROM information_schema.views
WHERE table_schema = 'public'
  AND table_name LIKE 'v_%';

-- インデックス一覧（新規作成分）
SELECT 'INDEX' AS type, indexname AS name
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname LIKE 'ix_%';


-- ############################################################
-- サンプルクエリ実行（動作確認）
-- ############################################################

-- 1) 運用KPIサマリ
SELECT * FROM public.v_ops_summary_24h;

-- 2) 失敗原因TOP
SELECT * FROM public.v_failed_reasons_7d ORDER BY cnt DESC LIMIT 10;

-- 3) dead一覧
SELECT * FROM public.v_retry_dead;

-- 4) スキップ理由
SELECT * FROM public.v_skip_summary ORDER BY cnt DESC;
