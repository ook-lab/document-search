-- ============================================================
-- dequeue_document から attachment_text を削除
-- ============================================================
-- 背景:
--   attachment_text カラムが Rawdata_FILE_AND_MAIL から削除されたが、
--   dequeue_document RPC がまだ参照しているため
--   "column t.attachment_text does not exist" エラーが発生している。
--   Python コードは attachment_text を使用していないため除去のみ。
-- ============================================================

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
    source_url TEXT,
    file_url TEXT,
    display_subject TEXT,
    display_post_text TEXT,
    display_sender TEXT,
    display_sender_email TEXT,
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
        t.source_url::TEXT,
        t.file_url::TEXT,
        t.display_subject::TEXT,
        t.display_post_text::TEXT,
        t.display_sender::TEXT,
        t.display_sender_email::TEXT,
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
'リース方式デキュー v7: attachment_text を除去（カラム削除済み）。';

DO $$
BEGIN
    RAISE NOTICE '✅ 20260227000001_drop_attachment_text_from_dequeue.sql 適用完了';
    RAISE NOTICE '  - dequeue_document: attachment_text を除去（v7）';
END $$;
