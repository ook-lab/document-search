-- ============================================
-- queued ステータス導入
-- ============================================
--
-- 【新しいステータス遷移】
-- pending → queued → processing → completed/failed
--           ↑
--        ランリストに乗った
--
-- 【操作】
-- - キューに追加: pending → queued（1件ずつ）
-- - 実行: Workerがqueuedを順番に消化
-- - 停止: queuedを全部pendingに戻す
--
-- 【特徴】
-- - 束ねない（1件1件独立）
-- - 追加し放題（処理中でも）
-- - 停止 = キュークリア（processingは流す）

-- ============================================
-- 1. dequeue_document を queued から取るように変更
-- ============================================

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
    source_id TEXT,
    display_subject TEXT,
    display_post_text TEXT,
    attachment_text TEXT,
    display_sender TEXT,
    display_sender_email TEXT,
    display_type TEXT,
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
    -- p_owner 必須チェック
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[dequeue_document] p_owner is required.';
    END IF;

    -- UPDATE...RETURNING で完全原子化
    -- 【変更】queued から取る（pendingではない）
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
            -- 【変更】queued または リース期限切れ
            (
                r.processing_status = 'queued'
                OR (r.processing_status = 'processing' AND r.lease_until < now())
            )
            -- workspace フィルタ
            AND (p_workspace = 'all' OR r.workspace = p_workspace)
            -- 試行回数上限
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
        t.source_id::TEXT,
        t.display_subject::TEXT,
        t.display_post_text::TEXT,
        t.attachment_text::TEXT,
        t.display_sender::TEXT,
        t.display_sender_email::TEXT,
        t.display_type::TEXT,
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
'リース方式デキュー v3: queued から取る。pending → queued → processing の遷移。';

-- ============================================
-- 2. enqueue_documents: pending → queued（キューに追加）
-- ============================================

CREATE OR REPLACE FUNCTION enqueue_documents(
    p_workspace TEXT DEFAULT 'all',
    p_limit INT DEFAULT 100,
    p_doc_ids UUID[] DEFAULT NULL
)
RETURNS TABLE (
    enqueued_count INT,
    doc_ids UUID[]
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INT;
    v_ids UUID[];
BEGIN
    -- 指定IDがあればそれを使用、なければworkspace条件で取得
    IF p_doc_ids IS NOT NULL AND array_length(p_doc_ids, 1) > 0 THEN
        -- 指定IDをqueued化
        WITH updated AS (
            UPDATE "Rawdata_FILE_AND_MAIL"
            SET
                processing_status = 'queued',
                updated_at = now()
            WHERE
                id = ANY(p_doc_ids)
                AND processing_status = 'pending'
            RETURNING id
        )
        SELECT COUNT(*)::INT, ARRAY_AGG(id) INTO v_count, v_ids FROM updated;
    ELSE
        -- workspace条件でpendingをqueued化
        WITH updated AS (
            UPDATE "Rawdata_FILE_AND_MAIL"
            SET
                processing_status = 'queued',
                updated_at = now()
            WHERE id IN (
                SELECT r.id
                FROM "Rawdata_FILE_AND_MAIL" r
                WHERE
                    r.processing_status = 'pending'
                    AND (p_workspace = 'all' OR r.workspace = p_workspace)
                    AND COALESCE(r.attempt_count, 0) < 5
                ORDER BY r.created_at ASC
                LIMIT p_limit
            )
            RETURNING id
        )
        SELECT COUNT(*)::INT, ARRAY_AGG(id) INTO v_count, v_ids FROM updated;
    END IF;

    RETURN QUERY SELECT v_count, v_ids;
END;
$$;

REVOKE ALL ON FUNCTION enqueue_documents(TEXT, INT, UUID[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION enqueue_documents(TEXT, INT, UUID[]) TO service_role;
GRANT EXECUTE ON FUNCTION enqueue_documents(TEXT, INT, UUID[]) TO authenticated;

COMMENT ON FUNCTION enqueue_documents IS
'キューに追加: pending → queued。1件ずつでも複数でもOK。';

-- ============================================
-- 3. clear_queue: queued → pending（停止=キュークリア）
-- ============================================

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
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = 'pending',
        updated_at = now()
    WHERE
        processing_status = 'queued'
        AND (p_workspace = 'all' OR workspace = p_workspace);

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN QUERY SELECT v_count;
END;
$$;

REVOKE ALL ON FUNCTION clear_queue(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION clear_queue(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION clear_queue(TEXT) TO authenticated;

COMMENT ON FUNCTION clear_queue IS
'停止: queued → pending に戻す。processingは流す（自然停止）。';

-- ============================================
-- 4. get_queue_status: キューの状態を取得
-- ============================================

CREATE OR REPLACE FUNCTION get_queue_status(
    p_workspace TEXT DEFAULT 'all'
)
RETURNS TABLE (
    pending_count BIGINT,
    queued_count BIGINT,
    processing_count BIGINT,
    completed_count BIGINT,
    failed_count BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*) FILTER (WHERE processing_status = 'pending'),
        COUNT(*) FILTER (WHERE processing_status = 'queued'),
        COUNT(*) FILTER (WHERE processing_status = 'processing'),
        COUNT(*) FILTER (WHERE processing_status = 'completed'),
        COUNT(*) FILTER (WHERE processing_status = 'failed')
    FROM "Rawdata_FILE_AND_MAIL"
    WHERE p_workspace = 'all' OR workspace = p_workspace;
END;
$$;

REVOKE ALL ON FUNCTION get_queue_status(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION get_queue_status(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION get_queue_status(TEXT) TO authenticated;

COMMENT ON FUNCTION get_queue_status IS
'キューの状態を取得: pending/queued/processing/completed/failed の件数';

-- ============================================
-- 5. インデックス追加（queued検索用）
-- ============================================

CREATE INDEX IF NOT EXISTS idx_rawdata_queued
    ON "Rawdata_FILE_AND_MAIL"(created_at ASC)
    WHERE processing_status = 'queued';

-- ============================================
-- 完了ログ
-- ============================================

DO $$
BEGIN
    RAISE NOTICE '✅ queued_status.sql 適用完了';
    RAISE NOTICE '  - dequeue_document: queued から取るように変更';
    RAISE NOTICE '  - enqueue_documents: pending → queued';
    RAISE NOTICE '  - clear_queue: queued → pending（停止）';
    RAISE NOTICE '  - get_queue_status: 状態取得';
    RAISE NOTICE '  - idx_rawdata_queued: インデックス追加';
END $$;
