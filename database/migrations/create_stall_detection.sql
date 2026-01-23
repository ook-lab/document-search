-- ============================================================
-- 停滞検知 + 整合性自動修復
--
-- A. 運用トレース列追加
-- B. 停滞検知VIEW (v_stalled_jobs)
-- C. 整合性VIEW (v_state_inconsistencies)
-- D. 自動修復関数 (reconcile_ops_state)
--
-- 実行: Supabase SQL Editor で実行
-- ============================================================


-- ############################################################
-- PART A: 運用トレース列を追加
-- ############################################################

ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS processing_started_at timestamptz;

ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS processing_heartbeat_at timestamptz;

ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS processing_worker_id text;


-- ############################################################
-- PART A-2: processing遷移時にstarted_at/heartbeat_atをセット
-- ############################################################

CREATE OR REPLACE FUNCTION public.track_processing_start()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- processing への遷移時
    IF NEW.processing_status = 'processing' AND
       (OLD.processing_status IS NULL OR OLD.processing_status != 'processing') THEN
        NEW.processing_started_at := now();
        NEW.processing_heartbeat_at := now();
    END IF;

    -- processing 中の更新（heartbeat更新）
    IF NEW.processing_status = 'processing' AND OLD.processing_status = 'processing' THEN
        NEW.processing_heartbeat_at := now();
    END IF;

    -- completed/failed/skipped への遷移時はクリア（次回処理用）
    IF NEW.processing_status IN ('completed', 'failed', 'skipped') AND
       OLD.processing_status = 'processing' THEN
        -- started_at は履歴として残す、heartbeat はクリア
        NEW.processing_heartbeat_at := NULL;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_track_processing ON public."Rawdata_FILE_AND_MAIL";

CREATE TRIGGER trg_track_processing
BEFORE UPDATE ON public."Rawdata_FILE_AND_MAIL"
FOR EACH ROW
EXECUTE FUNCTION public.track_processing_start();


-- ############################################################
-- PART B: 停滞検知VIEW
-- ############################################################

CREATE OR REPLACE VIEW public.v_stalled_jobs AS
-- pending で1時間以上
SELECT
    'pending_stalled' AS stall_type,
    r.id AS rawdata_id,
    r.file_name,
    r.processing_status,
    r.created_at,
    r.processing_started_at,
    r.processing_heartbeat_at,
    extract(epoch from (now() - r.created_at)) / 3600 AS hours_since_created,
    NULL::float AS hours_since_heartbeat,
    rq.status AS retry_status,
    rq.next_retry_at,
    rq.retry_count,
    left(coalesce(r.error_message, rq.last_error), 200) AS last_error
FROM public."Rawdata_FILE_AND_MAIL" r
LEFT JOIN public.retry_queue rq ON rq.rawdata_id = r.id
WHERE r.processing_status = 'pending'
  AND r.created_at < now() - interval '1 hour'

UNION ALL

-- processing で30分以上heartbeatなし
SELECT
    'processing_stalled' AS stall_type,
    r.id AS rawdata_id,
    r.file_name,
    r.processing_status,
    r.created_at,
    r.processing_started_at,
    r.processing_heartbeat_at,
    extract(epoch from (now() - r.created_at)) / 3600 AS hours_since_created,
    extract(epoch from (now() - coalesce(r.processing_heartbeat_at, r.processing_started_at))) / 3600 AS hours_since_heartbeat,
    rq.status AS retry_status,
    rq.next_retry_at,
    rq.retry_count,
    left(coalesce(r.error_message, rq.last_error), 200) AS last_error
FROM public."Rawdata_FILE_AND_MAIL" r
LEFT JOIN public.retry_queue rq ON rq.rawdata_id = r.id
WHERE r.processing_status = 'processing'
  AND coalesce(r.processing_heartbeat_at, r.processing_started_at, r.created_at) < now() - interval '30 minutes'

UNION ALL

-- retry_queue が queued で next_retry_at が過去（放置）
SELECT
    'retry_overdue' AS stall_type,
    r.id AS rawdata_id,
    r.file_name,
    r.processing_status,
    r.created_at,
    r.processing_started_at,
    r.processing_heartbeat_at,
    extract(epoch from (now() - r.created_at)) / 3600 AS hours_since_created,
    NULL::float AS hours_since_heartbeat,
    rq.status AS retry_status,
    rq.next_retry_at,
    rq.retry_count,
    left(rq.last_error, 200) AS last_error
FROM public.retry_queue rq
JOIN public."Rawdata_FILE_AND_MAIL" r ON r.id = rq.rawdata_id
WHERE rq.status = 'queued'
  AND rq.next_retry_at < now() - interval '10 minutes';


-- ############################################################
-- PART C: 整合性VIEW
-- ############################################################

CREATE OR REPLACE VIEW public.v_state_inconsistencies AS
-- completed なのに retry_queue に queued/leased が残っている
SELECT
    'completed_but_queued' AS inconsistency_type,
    r.id AS rawdata_id,
    r.file_name,
    r.processing_status,
    rq.status AS retry_status,
    rq.retry_count,
    'retry_queue should be done or removed' AS action_needed
FROM public."Rawdata_FILE_AND_MAIL" r
JOIN public.retry_queue rq ON rq.rawdata_id = r.id
WHERE r.processing_status = 'completed'
  AND rq.status IN ('queued', 'leased')

UNION ALL

-- failed なのに retry_queue に存在しない（回収漏れ）
SELECT
    'failed_not_queued' AS inconsistency_type,
    r.id AS rawdata_id,
    r.file_name,
    r.processing_status,
    NULL AS retry_status,
    NULL AS retry_count,
    'should be enqueued for retry' AS action_needed
FROM public."Rawdata_FILE_AND_MAIL" r
LEFT JOIN public.retry_queue rq ON rq.rawdata_id = r.id
WHERE r.processing_status = 'failed'
  AND rq.rawdata_id IS NULL

UNION ALL

-- leased なのに leased_until が過去（孤児lease）
SELECT
    'orphan_lease' AS inconsistency_type,
    r.id AS rawdata_id,
    r.file_name,
    r.processing_status,
    rq.status AS retry_status,
    rq.retry_count,
    'should reset to queued' AS action_needed
FROM public.retry_queue rq
JOIN public."Rawdata_FILE_AND_MAIL" r ON r.id = rq.rawdata_id
WHERE rq.status = 'leased'
  AND rq.leased_until < now();


-- ############################################################
-- PART D: 自動修復関数
-- ############################################################

CREATE OR REPLACE FUNCTION public.reconcile_ops_state(
    dry_run boolean DEFAULT true,
    max_rows int DEFAULT 500
)
RETURNS TABLE (
    action text,
    rawdata_id uuid,
    detail text,
    affected int
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_completed_but_queued int := 0;
    v_failed_not_queued int := 0;
    v_orphan_lease int := 0;
BEGIN
    -- 1) completed なのに retry_queue に残っている → done に更新
    IF dry_run THEN
        SELECT count(*) INTO v_completed_but_queued
        FROM public."Rawdata_FILE_AND_MAIL" r
        JOIN public.retry_queue rq ON rq.rawdata_id = r.id
        WHERE r.processing_status = 'completed'
          AND rq.status IN ('queued', 'leased');

        RETURN QUERY SELECT
            'completed_but_queued (dry_run)'::text,
            NULL::uuid,
            'Would mark retry_queue as done'::text,
            v_completed_but_queued;
    ELSE
        WITH updated AS (
            UPDATE public.retry_queue rq
            SET status = 'done', updated_at = now()
            FROM public."Rawdata_FILE_AND_MAIL" r
            WHERE rq.rawdata_id = r.id
              AND r.processing_status = 'completed'
              AND rq.status IN ('queued', 'leased')
            RETURNING rq.rawdata_id
        )
        SELECT count(*) INTO v_completed_but_queued FROM updated;

        RETURN QUERY SELECT
            'completed_but_queued (fixed)'::text,
            NULL::uuid,
            'Marked retry_queue as done'::text,
            v_completed_but_queued;
    END IF;

    -- 2) failed なのに retry_queue が無い → enqueue
    IF dry_run THEN
        SELECT count(*) INTO v_failed_not_queued
        FROM public."Rawdata_FILE_AND_MAIL" r
        LEFT JOIN public.retry_queue rq ON rq.rawdata_id = r.id
        WHERE r.processing_status = 'failed'
          AND rq.rawdata_id IS NULL;

        RETURN QUERY SELECT
            'failed_not_queued (dry_run)'::text,
            NULL::uuid,
            'Would enqueue for retry'::text,
            v_failed_not_queued;
    ELSE
        WITH inserted AS (
            INSERT INTO public.retry_queue (rawdata_id, last_error, next_retry_at)
            SELECT r.id, r.error_message, now()
            FROM public."Rawdata_FILE_AND_MAIL" r
            LEFT JOIN public.retry_queue rq ON rq.rawdata_id = r.id
            WHERE r.processing_status = 'failed'
              AND rq.rawdata_id IS NULL
            LIMIT max_rows
            RETURNING rawdata_id
        )
        SELECT count(*) INTO v_failed_not_queued FROM inserted;

        RETURN QUERY SELECT
            'failed_not_queued (fixed)'::text,
            NULL::uuid,
            'Enqueued for retry'::text,
            v_failed_not_queued;
    END IF;

    -- 3) 孤児lease → queued に戻す
    IF dry_run THEN
        SELECT count(*) INTO v_orphan_lease
        FROM public.retry_queue
        WHERE status = 'leased'
          AND leased_until < now();

        RETURN QUERY SELECT
            'orphan_lease (dry_run)'::text,
            NULL::uuid,
            'Would reset to queued'::text,
            v_orphan_lease;
    ELSE
        WITH updated AS (
            UPDATE public.retry_queue
            SET status = 'queued', leased_until = NULL, updated_at = now()
            WHERE status = 'leased'
              AND leased_until < now()
            RETURNING rawdata_id
        )
        SELECT count(*) INTO v_orphan_lease FROM updated;

        RETURN QUERY SELECT
            'orphan_lease (fixed)'::text,
            NULL::uuid,
            'Reset to queued'::text,
            v_orphan_lease;
    END IF;
END;
$$;


-- ############################################################
-- 権限付与
-- ############################################################

GRANT SELECT ON public.v_stalled_jobs TO anon, authenticated;
GRANT SELECT ON public.v_state_inconsistencies TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reconcile_ops_state(boolean, int) TO authenticated;


-- ############################################################
-- インデックス追加（停滞検知の高速化）
-- ############################################################

CREATE INDEX IF NOT EXISTS ix_rawdata_pending_created
ON public."Rawdata_FILE_AND_MAIL"(created_at)
WHERE processing_status = 'pending';

CREATE INDEX IF NOT EXISTS ix_rawdata_processing_heartbeat
ON public."Rawdata_FILE_AND_MAIL"(processing_heartbeat_at)
WHERE processing_status = 'processing';


-- ############################################################
-- 検証クエリ
-- ############################################################

-- カラム確認
SELECT 'トレース列' AS check_item, column_name AS name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'Rawdata_FILE_AND_MAIL'
  AND column_name IN ('processing_started_at', 'processing_heartbeat_at', 'processing_worker_id');

-- VIEW確認
SELECT 'VIEW' AS check_item, table_name AS name
FROM information_schema.views
WHERE table_schema = 'public'
  AND table_name IN ('v_stalled_jobs', 'v_state_inconsistencies');

-- 関数確認
SELECT 'FUNCTION' AS check_item, proname AS name
FROM pg_proc
JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace
WHERE nspname = 'public'
  AND proname IN ('track_processing_start', 'reconcile_ops_state');
