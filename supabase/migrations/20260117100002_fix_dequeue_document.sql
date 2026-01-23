-- ============================================
-- FIX: dequeue_document の型不一致とカラム修正
-- ============================================
-- 修正1: classroom_sender_email 削除（存在しない）
-- 修正2: mimeType 削除（存在しない）
-- 修正3: varchar → TEXT キャスト追加
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
AS $$
DECLARE
    v_row "Rawdata_FILE_AND_MAIL"%ROWTYPE;
BEGIN
    -- 対象行を1件だけ取得してロック（FOR UPDATE SKIP LOCKED で競合回避）
    SELECT * INTO v_row
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
    FOR UPDATE SKIP LOCKED;  -- 他ワーカーがロック中の行はスキップ

    -- 対象がなければ NULL を返す
    IF v_row.id IS NULL THEN
        RETURN;
    END IF;

    -- 対象行を更新（processing + リース設定）
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = 'processing',
        lease_owner = p_owner,
        lease_until = now() + make_interval(secs => p_lease_seconds),
        attempt_count = COALESCE("Rawdata_FILE_AND_MAIL".attempt_count, 0) + 1,
        updated_at = now()
    WHERE "Rawdata_FILE_AND_MAIL".id = v_row.id;

    -- 更新後の行を返す（varchar → TEXT キャスト）
    RETURN QUERY
    SELECT
        v_row.id,
        v_row.file_name::TEXT,
        v_row.title::TEXT,
        v_row.workspace::TEXT,
        v_row.doc_type::TEXT,
        'processing'::TEXT,
        v_row.source_id::TEXT,
        v_row.display_subject::TEXT,
        v_row.display_post_text::TEXT,
        v_row.attachment_text::TEXT,
        v_row.display_sender::TEXT,
        v_row.display_sender_email::TEXT,
        v_row.display_type::TEXT,
        v_row.display_sent_at,
        v_row.screenshot_url::TEXT,
        v_row.owner_id,
        p_owner,
        now() + make_interval(secs => p_lease_seconds),
        COALESCE(v_row.attempt_count, 0) + 1;
END;
$$;

-- 権限設定
REVOKE ALL ON FUNCTION dequeue_document(TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dequeue_document(TEXT, INT, TEXT) TO service_role;

COMMENT ON FUNCTION dequeue_document IS 'リース方式デキュー: 1件取得 + processing + リース設定を原子化';
