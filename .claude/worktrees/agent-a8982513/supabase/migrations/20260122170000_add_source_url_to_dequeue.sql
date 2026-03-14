-- ============================================
-- dequeue_document に source_url を追加
-- ============================================
--
-- 【背景】
-- source_id は Classroom の内部ID形式 (例: 835208544969:attachment:0)
-- source_url は Google Drive の実際のURL (例: https://drive.google.com/file/d/{FILE_ID}/view)
-- ワーカーが source_url からファイルIDを抽出してダウンロードする必要がある

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
    source_url TEXT,  -- 追加: Google Drive URL
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
    -- queued から取る（pendingではない）
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
            -- queued または リース期限切れ
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
        t.source_url::TEXT,  -- 追加
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
'リース方式デキュー v4: source_url を追加。Google Drive URLからファイルIDを抽出可能に。';

-- ============================================
-- 完了ログ
-- ============================================

DO $$
BEGIN
    RAISE NOTICE '✅ add_source_url_to_dequeue.sql 適用完了';
    RAISE NOTICE '  - dequeue_document: source_url カラムを追加';
END $$;
