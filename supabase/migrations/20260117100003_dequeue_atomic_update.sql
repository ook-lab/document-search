-- ============================================
-- 指示1: dequeue を UPDATE...RETURNING に統合（原子的操作）
-- 指示2: SECURITY DEFINER に search_path 固定
-- 指示3: p_owner 必須化（NULL 禁止）
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
SET search_path = public  -- 指示2: search_path 固定
AS $$
BEGIN
    -- 指示3: p_owner 必須チェック
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[dequeue_document] p_owner is required. NULL or empty owner is not allowed for traceability.';
    END IF;

    -- 指示1: UPDATE...RETURNING で完全原子化
    -- サブクエリで対象行を特定 + ロック → UPDATE → RETURNING で更新後の値を返却
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
            -- 条件1: pending または リース期限切れ
            (
                r.processing_status = 'pending'
                OR (r.processing_status = 'processing' AND r.lease_until < now())
            )
            -- 条件2: workspace フィルタ
            AND (p_workspace = 'all' OR r.workspace = p_workspace)
            -- 条件3: 試行回数上限（5回超えたら取らない）
            AND COALESCE(r.attempt_count, 0) < 5
        ORDER BY r.created_at ASC  -- 古いものから処理
        LIMIT 1
        FOR UPDATE SKIP LOCKED  -- 他ワーカーがロック中の行はスキップ
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

-- 権限設定
REVOKE ALL ON FUNCTION dequeue_document(TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dequeue_document(TEXT, INT, TEXT) TO service_role;

COMMENT ON FUNCTION dequeue_document IS
'リース方式デキュー v2: UPDATE...RETURNING で完全原子化。p_owner 必須。search_path 固定。';
