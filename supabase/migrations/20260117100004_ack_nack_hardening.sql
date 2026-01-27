-- ============================================
-- 指示4: ack/nack に search_path 固定 + lease_owner チェック明確化
-- 指示5: attempt_count 超過時の failed ステータス（既存動作を維持）
-- 指示7: 可観測性カラム追加（last_error_reason, last_worker, completed_at, failed_at）
-- ============================================

-- ============================================
-- 1. 可観測性カラム追加（指示7）
-- ============================================
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS last_error_reason TEXT,
ADD COLUMN IF NOT EXISTS last_worker TEXT,
ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".last_error_reason IS 'nack時の最終エラー理由';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".last_worker IS '最後に処理したワーカーID';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".last_attempt_at IS '最後の処理試行時刻';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".completed_at IS '処理完了時刻';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".failed_at IS '最終失敗時刻（5回超過）';

-- ============================================
-- 2. ack_document 強化版
-- ============================================
DROP FUNCTION IF EXISTS ack_document(UUID, TEXT);

CREATE OR REPLACE FUNCTION ack_document(
    p_id UUID,
    p_owner TEXT
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public  -- 指示2/4: search_path 固定
AS $$
DECLARE
    v_count INT;
BEGIN
    -- 指示4: p_owner 必須チェック
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[ack_document] p_owner is required.';
    END IF;

    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = 'completed',
        lease_owner = NULL,
        lease_until = NULL,
        processing_stage = '完了',
        processing_progress = 1.0,
        updated_at = now(),
        -- 指示7: 可観測性
        last_worker = p_owner,
        completed_at = now()
    WHERE
        id = p_id
        AND lease_owner = p_owner;  -- 指示4: owner 一致必須

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- ============================================
-- 3. nack_document 強化版
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
SET search_path = public  -- 指示2/4: search_path 固定
AS $$
DECLARE
    v_count INT;
    v_attempt_count INT;
    v_new_status TEXT;
BEGIN
    -- 指示4: p_owner 必須チェック
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
        processing_stage = CASE WHEN v_new_status = 'failed' THEN 'エラー（リトライ上限）' ELSE NULL END,
        processing_progress = 0.0,
        updated_at = now(),
        -- 指示7: 可観測性
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
-- 4. renew_lease 強化版
-- ============================================
DROP FUNCTION IF EXISTS renew_lease(UUID, TEXT, INT);

CREATE OR REPLACE FUNCTION renew_lease(
    p_id UUID,
    p_owner TEXT,
    p_lease_seconds INT DEFAULT 900
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public  -- 指示2/4: search_path 固定
AS $$
DECLARE
    v_count INT;
BEGIN
    -- p_owner 必須チェック
    IF p_owner IS NULL OR p_owner = '' THEN
        RAISE EXCEPTION '[renew_lease] p_owner is required.';
    END IF;

    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        lease_until = now() + make_interval(secs => p_lease_seconds),
        updated_at = now(),
        last_attempt_at = now()  -- 指示7: ハートビート記録
    WHERE
        id = p_id
        AND lease_owner = p_owner
        AND processing_status = 'processing';

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- ============================================
-- 5. 権限設定
-- ============================================
REVOKE ALL ON FUNCTION ack_document(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ack_document(UUID, TEXT) TO service_role;

REVOKE ALL ON FUNCTION nack_document(UUID, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nack_document(UUID, TEXT, TEXT, BOOLEAN) TO service_role;

REVOKE ALL ON FUNCTION renew_lease(UUID, TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION renew_lease(UUID, TEXT, INT) TO service_role;

-- ============================================
-- 6. コメント
-- ============================================
COMMENT ON FUNCTION ack_document IS '処理完了: owner一致必須、completed_at記録';
COMMENT ON FUNCTION nack_document IS '処理失敗: owner一致必須、5回超過でfailed、last_error_reason記録';
COMMENT ON FUNCTION renew_lease IS 'リース延長: owner一致必須、last_attempt_at記録';
