-- ============================================
-- processing_stage カラム削除に伴う RPC 修正
-- processing_stage は processing_status + processing_progress で代替
-- ============================================

-- ============================================
-- 1. ack_document: processing_stage を削除
-- ============================================
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
    -- p_owner 必須チェック
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[ack_document] p_owner is required.';
    END IF;

    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = 'completed',
        lease_owner = NULL,
        lease_until = NULL,
        -- processing_stage = '完了', -- 削除済み
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

-- ============================================
-- 2. nack_document: processing_stage を削除
-- ============================================
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
    v_attempt_count INT;
    v_new_status TEXT;
BEGIN
    -- p_owner 必須チェック
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[nack_document] p_owner is required.';
    END IF;

    -- 現在の attempt_count を取得（owner 一致確認も兼ねる）
    SELECT attempt_count INTO v_attempt_count
    FROM "Rawdata_FILE_AND_MAIL"
    WHERE id = p_id AND lease_owner = p_owner;

    IF v_attempt_count IS NULL THEN
        RETURN 0;  -- owner不一致または既に回収済み
    END IF;

    -- リトライなし: 1回失敗したら即failed
    IF v_attempt_count >= 1 OR NOT p_retry THEN
        v_new_status := 'failed';
    ELSE
        v_new_status := 'pending';
    END IF;

    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = v_new_status,
        lease_owner = NULL,
        lease_until = NULL,
        -- processing_stage = CASE WHEN v_new_status = 'failed' THEN 'エラー（リトライ上限）' ELSE NULL END, -- 削除済み
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

-- ============================================
-- 3. コメント更新
-- ============================================
COMMENT ON FUNCTION ack_document IS '処理完了: owner一致必須、completed_at記録、processing_status=completed';
COMMENT ON FUNCTION nack_document IS '処理失敗: owner一致必須、1回超過でfailed、last_error_reason記録';
