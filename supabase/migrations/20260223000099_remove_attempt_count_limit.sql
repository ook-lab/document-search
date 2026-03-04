-- ============================================
-- attempt_count による回数制限を完全撤廃
-- ============================================
--
-- 変更内容:
--   1. dequeue_document : attempt_count < 5 除去
--                         return type を 20260122170000 の正確なコピーに修正
--                         （source_url あり、classroom_sender_email/mimeType なし）
--   2. enqueue_documents: attempt_count < 5 除去
--   3. ack_document     : processing_stage 参照除去（確実に適用）
--   4. nack_document    : attempt_count >= 1 → failed ロジックを除去
--                         p_retry フラグのみで判定
-- ============================================

-- ============================================
-- 1. dequeue_document
--    20260122170000_add_source_url_to_dequeue.sql の正確なコピー
--    ただし attempt_count < 5 のみ除去
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
    source_url TEXT,
    display_subject TEXT,
    display_post_text TEXT,
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
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[dequeue_document] p_owner is required.';
    END IF;

    -- UPDATE...RETURNING で完全原子化
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
            -- attempt_count < 5 を除去（回数制限なし）
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
        t.source_url::TEXT,
        t.display_subject::TEXT,
        t.display_post_text::TEXT,
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
'リース方式デキュー v5: attempt_count 上限なし。queued から取る。source_url あり。';

-- ============================================
-- 2. enqueue_documents
--    20260118000001_queued_status.sql の正確なコピー
--    ただし workspace 条件の attempt_count < 5 のみ除去
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
        -- workspace条件でpendingをqueued化（attempt_count < 5 を除去）
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
                    -- attempt_count < 5 を除去（回数制限なし）
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
'キューに追加: pending → queued。attempt_count 上限なし。';

-- ============================================
-- 3. ack_document
--    processing_stage 参照なし（確実に適用）
-- ============================================
DROP FUNCTION IF EXISTS ack_document(UUID, TEXT);

CREATE OR REPLACE FUNCTION ack_document(
    p_id UUID,
    p_owner TEXT
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
        RAISE EXCEPTION '[ack_document] p_owner is required.';
    END IF;

    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = 'completed',
        lease_owner = NULL,
        lease_until = NULL,
        processing_progress = 1.0,
        updated_at = now(),
        last_worker = p_owner,
        completed_at = now()
    WHERE
        id = p_id
        AND lease_owner = p_owner;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION ack_document(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ack_document(UUID, TEXT) TO service_role;

-- ============================================
-- 4. nack_document
--    attempt_count >= 1 → failed ロジックを除去
--    p_retry フラグのみで判定
-- ============================================
DROP FUNCTION IF EXISTS nack_document(UUID, TEXT, TEXT, BOOLEAN);

CREATE OR REPLACE FUNCTION nack_document(
    p_id UUID,
    p_owner TEXT,
    p_error_message TEXT DEFAULT NULL,
    p_retry BOOLEAN DEFAULT TRUE
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INT;
    v_new_status TEXT;
BEGIN
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[nack_document] p_owner is required.';
    END IF;

    -- p_retry フラグのみで判定（attempt_count による回数制限なし）
    IF NOT p_retry THEN
        v_new_status := 'failed';
    ELSE
        v_new_status := 'pending';
    END IF;

    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = v_new_status,
        lease_owner = NULL,
        lease_until = NULL,
        processing_progress = 0.0,
        updated_at = now(),
        last_error_reason = p_error_message,
        last_worker = p_owner,
        last_attempt_at = now(),
        failed_at = CASE WHEN v_new_status = 'failed' THEN now() ELSE failed_at END
    WHERE
        id = p_id
        AND lease_owner = p_owner;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION nack_document(UUID, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nack_document(UUID, TEXT, TEXT, BOOLEAN) TO service_role;

-- ============================================
-- 完了ログ
-- ============================================

DO $$
BEGIN
    RAISE NOTICE '✅ remove_attempt_count_limit.sql 適用完了';
    RAISE NOTICE '  - dequeue_document: attempt_count 上限除去、return type 修正（source_url あり）';
    RAISE NOTICE '  - enqueue_documents: attempt_count 上限除去';
    RAISE NOTICE '  - ack_document: processing_stage 参照なし';
    RAISE NOTICE '  - nack_document: attempt_count >= 1 条件除去、p_retry フラグのみで判定';
END $$;
