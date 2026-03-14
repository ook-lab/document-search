-- ============================================================
-- 早稲田アカデミーデータ移行
-- Rawdata_FILE_AND_MAIL → 05_ikuya_waseaca_01_raw + pipeline_meta
-- ============================================================
-- 実行順:
--   1-1. pipeline_meta にキュー管理 + G中間データカラムを追加
--   1-2. pipeline_meta 用 RPC 4本を定義
--   1-3. 既存早稲アカデータを移行し Rawdata_FILE_AND_MAIL から削除
-- ============================================================

-- ============================================================
-- 1-1. pipeline_meta カラム追加
-- ============================================================

ALTER TABLE public.pipeline_meta
  ADD COLUMN IF NOT EXISTS lease_owner             TEXT,
  ADD COLUMN IF NOT EXISTS lease_until             TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS attempt_count           INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS error_message           TEXT,
  ADD COLUMN IF NOT EXISTS last_error_reason       TEXT,
  ADD COLUMN IF NOT EXISTS failed_at               TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS started_at              TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS completed_at            TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS g14_reconstructed_tables JSONB,
  ADD COLUMN IF NOT EXISTS g17_table_analyses      JSONB,
  ADD COLUMN IF NOT EXISTS g21_articles            JSONB,
  ADD COLUMN IF NOT EXISTS g22_ai_extracted        JSONB;

CREATE INDEX IF NOT EXISTS idx_pm_queue_lease
  ON public.pipeline_meta (processing_status, lease_until)
  WHERE processing_status IN ('pending', 'processing');

-- ============================================================
-- 1-2. RPC: dequeue_pipeline（原子化デキュー）
-- ============================================================
-- raw_table を指定してキューから1件取得し、
-- processing + リース設定を1アトミック操作で行う
-- ============================================================

DROP FUNCTION IF EXISTS dequeue_pipeline(TEXT, INT, TEXT);

CREATE OR REPLACE FUNCTION dequeue_pipeline(
    p_raw_table    TEXT,
    p_lease_seconds INT DEFAULT 900,
    p_owner        TEXT DEFAULT NULL
)
RETURNS TABLE (
    meta_id      UUID,
    raw_id       UUID,
    raw_table    TEXT,
    person       TEXT,
    source       TEXT,
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
                m.processing_status = 'pending'
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
'pipeline_meta リース方式デキュー: raw_table 指定で1件取得し processing + リース設定を原子化';

-- ============================================================
-- RPC: ack_pipeline（処理完了）
-- ============================================================

DROP FUNCTION IF EXISTS ack_pipeline(UUID, TEXT);

CREATE OR REPLACE FUNCTION ack_pipeline(
    p_meta_id UUID,
    p_owner   TEXT
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INT;
BEGIN
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[ack_pipeline] p_owner is required.';
    END IF;

    UPDATE public.pipeline_meta
    SET
        processing_status  = 'completed',
        processing_progress = 1.0,
        lease_owner        = NULL,
        lease_until        = NULL,
        updated_at         = now(),
        completed_at       = now()
    WHERE
        id          = p_meta_id
        AND lease_owner = p_owner;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION ack_pipeline(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ack_pipeline(UUID, TEXT) TO service_role;

COMMENT ON FUNCTION ack_pipeline IS 'pipeline_meta 処理完了: owner 一致必須、completed_at 記録';

-- ============================================================
-- RPC: nack_pipeline（処理失敗 / リトライ）
-- ============================================================

DROP FUNCTION IF EXISTS nack_pipeline(UUID, TEXT, TEXT, BOOLEAN);

CREATE OR REPLACE FUNCTION nack_pipeline(
    p_meta_id      UUID,
    p_owner        TEXT,
    p_error_message TEXT DEFAULT NULL,
    p_retry        BOOLEAN DEFAULT TRUE
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count      INT;
    v_new_status TEXT;
BEGIN
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[nack_pipeline] p_owner is required.';
    END IF;

    IF NOT p_retry THEN
        v_new_status := 'failed';
    ELSE
        v_new_status := 'pending';
    END IF;

    UPDATE public.pipeline_meta
    SET
        processing_status   = v_new_status,
        processing_progress = 0.0,
        lease_owner         = NULL,
        lease_until         = NULL,
        updated_at          = now(),
        last_error_reason   = p_error_message,
        failed_at           = CASE WHEN v_new_status = 'failed' THEN now() ELSE failed_at END
    WHERE
        id          = p_meta_id
        AND lease_owner = p_owner;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION nack_pipeline(UUID, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nack_pipeline(UUID, TEXT, TEXT, BOOLEAN) TO service_role;

COMMENT ON FUNCTION nack_pipeline IS 'pipeline_meta 処理失敗: p_retry=true→pending, false→failed';

-- ============================================================
-- RPC: renew_pipeline_lease（リース延長）
-- ============================================================

DROP FUNCTION IF EXISTS renew_pipeline_lease(UUID, TEXT, INT);

CREATE OR REPLACE FUNCTION renew_pipeline_lease(
    p_meta_id      UUID,
    p_owner        TEXT,
    p_lease_seconds INT DEFAULT 900
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INT;
BEGIN
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[renew_pipeline_lease] p_owner is required.';
    END IF;

    UPDATE public.pipeline_meta
    SET
        lease_until = now() + make_interval(secs => p_lease_seconds),
        updated_at  = now()
    WHERE
        id               = p_meta_id
        AND lease_owner  = p_owner
        AND processing_status = 'processing';

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION renew_pipeline_lease(UUID, TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION renew_pipeline_lease(UUID, TEXT, INT) TO service_role;

COMMENT ON FUNCTION renew_pipeline_lease IS 'pipeline_meta リース延長: ロングジョブ用ハートビート';

-- ============================================================
-- 1-3. 既存早稲アカデータの移行
-- ============================================================

-- Step A: 05_ikuya_waseaca_01_raw に INSERT（未登録分のみ）
INSERT INTO public."05_ikuya_waseaca_01_raw"
  (person, source, category, post_id, title, description,
   creator_name, file_url, file_name, created_at, ingested_at)
SELECT
  'ikuya',
  '早稲アカオンライン',
  r.metadata->>'notice_category',
  r.file_id,
  r.display_subject,
  r.display_post_text,
  r.display_sender,
  r.file_url,
  r.file_name,
  r.display_sent_at,
  r.created_at
FROM public."Rawdata_FILE_AND_MAIL" r
WHERE r.workspace = 'waseda_academy'
  AND NOT EXISTS (
    SELECT 1
    FROM public."05_ikuya_waseaca_01_raw" w
    WHERE w.post_id = r.file_id
  );

-- Step B: pipeline_meta に INSERT（05 の id を raw_id として、未登録分のみ）
INSERT INTO public.pipeline_meta
  (raw_id, raw_table, person, source,
   processing_status, processing_progress, attempt_count,
   g14_reconstructed_tables, g17_table_analyses,
   g21_articles, g22_ai_extracted)
SELECT
  w.id,
  '05_ikuya_waseaca_01_raw',
  'ikuya',
  '早稲アカオンライン',
  CASE r.processing_status
    WHEN 'completed' THEN 'completed'
    WHEN 'failed'    THEN 'failed'
    ELSE 'pending'
  END,
  COALESCE(r.processing_progress, 0.0),
  COALESCE(r.attempt_count, 0),
  r.g14_reconstructed_tables,
  r.g17_table_analyses,
  r.g21_articles,
  r.g22_ai_extracted
FROM public."Rawdata_FILE_AND_MAIL" r
JOIN public."05_ikuya_waseaca_01_raw" w ON w.post_id = r.file_id
ON CONFLICT (raw_id, raw_table) DO NOTHING;

-- Step C: Rawdata_FILE_AND_MAIL から早稲アカ行を削除
DELETE FROM public."Rawdata_FILE_AND_MAIL"
WHERE workspace = 'waseda_academy';

-- ============================================================
-- 完了ログ
-- ============================================================
DO $$
DECLARE
  v_raw_count  INT;
  v_meta_count INT;
BEGIN
  SELECT COUNT(*) INTO v_raw_count  FROM public."05_ikuya_waseaca_01_raw";
  SELECT COUNT(*) INTO v_meta_count FROM public.pipeline_meta WHERE raw_table = '05_ikuya_waseaca_01_raw';
  RAISE NOTICE '====================================================';
  RAISE NOTICE '早稲田アカデミーデータ移行完了';
  RAISE NOTICE '  05_ikuya_waseaca_01_raw 件数: %', v_raw_count;
  RAISE NOTICE '  pipeline_meta 件数       : %', v_meta_count;
  RAISE NOTICE '  Rawdata_FILE_AND_MAIL (waseda_academy): 0件に削除済み';
  RAISE NOTICE '====================================================';
END $$;
