-- ============================================
-- リース方式キュー: 100ワーカーでも重複ゼロ
-- ============================================
-- 目的: 同一ジョブを複数ワーカーが同時に処理できないようにする
-- 方式: dequeue を原子化し、リースで排他制御
-- ============================================

-- ============================================
-- 1. カラム追加（Rawdata_FILE_AND_MAIL）
-- ============================================

-- lease_owner: ワーカー識別子（hostname:pid:uuid）
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS lease_owner TEXT NULL;

-- lease_until: リースの有効期限
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ NULL;

-- attempt_count: 試行回数（無限再試行防止）
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS attempt_count INT NOT NULL DEFAULT 0;

-- updated_at: 更新日時（既存の場合はスキップ）
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- ============================================
-- 2. インデックス（pending取得を高速化）
-- ============================================
CREATE INDEX IF NOT EXISTS idx_rawdata_queue_status_lease
ON "Rawdata_FILE_AND_MAIL" (processing_status, lease_until)
WHERE processing_status IN ('pending', 'processing');

-- ============================================
-- 3. RPC: dequeue_document（原子化されたデキュー）
-- ============================================
-- 「SELECT → UPDATE」分離は禁止。1回の操作で完結。
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
    classroom_sender_email TEXT,
    screenshot_url TEXT,
    mimeType TEXT,
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
        AND r.attempt_count < 5
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
        attempt_count = "Rawdata_FILE_AND_MAIL".attempt_count + 1,
        updated_at = now()
    WHERE "Rawdata_FILE_AND_MAIL".id = v_row.id;

    -- 更新後の行を返す
    RETURN QUERY
    SELECT
        v_row.id,
        v_row.file_name,
        v_row.title,
        v_row.workspace,
        v_row.doc_type,
        'processing'::TEXT,
        v_row.source_id,
        v_row.display_subject,
        v_row.display_post_text,
        v_row.attachment_text,
        v_row.display_sender,
        v_row.display_sender_email,
        v_row.display_type,
        v_row.display_sent_at,
        v_row.classroom_sender_email,
        v_row.screenshot_url,
        v_row."mimeType",
        v_row.owner_id,
        p_owner,
        now() + make_interval(secs => p_lease_seconds),
        v_row.attempt_count + 1;
END;
$$;

-- ============================================
-- 4. RPC: ack_document（完了更新）
-- ============================================
-- owner条件付きで更新（横取り防止）
-- ============================================
DROP FUNCTION IF EXISTS ack_document(UUID, TEXT);
CREATE OR REPLACE FUNCTION ack_document(
    p_id UUID,
    p_owner TEXT
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_count INT;
BEGIN
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = 'completed',
        lease_owner = NULL,
        lease_until = NULL,
        processing_stage = '完了',
        processing_progress = 1.0,
        updated_at = now()
    WHERE
        id = p_id
        AND lease_owner = p_owner;  -- 必ず owner を確認

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- ============================================
-- 5. RPC: nack_document（失敗/リトライ）
-- ============================================
-- owner条件付きで更新（横取り防止）
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
AS $$
DECLARE
    v_count INT;
    v_attempt_count INT;
    v_new_status TEXT;
BEGIN
    -- 現在の attempt_count を取得
    SELECT attempt_count INTO v_attempt_count
    FROM "Rawdata_FILE_AND_MAIL"
    WHERE id = p_id AND lease_owner = p_owner;

    IF v_attempt_count IS NULL THEN
        RETURN 0;  -- owner不一致または既に回収済み
    END IF;

    -- 5回以上失敗したら failed、それ以外で retry=true なら pending
    IF v_attempt_count >= 5 OR NOT p_retry THEN
        v_new_status := 'failed';
    ELSE
        v_new_status := 'pending';
    END IF;

    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        processing_status = v_new_status,
        lease_owner = NULL,
        lease_until = NULL,
        processing_stage = CASE WHEN v_new_status = 'failed' THEN 'エラー' ELSE NULL END,
        processing_progress = 0.0,
        updated_at = now(),
        -- metadata に last_error を追加
        metadata = COALESCE(metadata, '{}'::jsonb) ||
            jsonb_build_object(
                'last_error', p_error_message,
                'last_error_time', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS')
            )
    WHERE
        id = p_id
        AND lease_owner = p_owner;  -- 必ず owner を確認

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- ============================================
-- 6. RPC: renew_lease（リース延長）
-- ============================================
-- ロングジョブ用ハートビート
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
AS $$
DECLARE
    v_count INT;
BEGIN
    UPDATE "Rawdata_FILE_AND_MAIL"
    SET
        lease_until = now() + make_interval(secs => p_lease_seconds),
        updated_at = now()
    WHERE
        id = p_id
        AND lease_owner = p_owner
        AND processing_status = 'processing';

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- ============================================
-- 7. RPC 権限設定
-- ============================================
-- service_role のみ実行可能（anon からは呼べない）
REVOKE ALL ON FUNCTION dequeue_document(TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dequeue_document(TEXT, INT, TEXT) TO service_role;

REVOKE ALL ON FUNCTION ack_document(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ack_document(UUID, TEXT) TO service_role;

REVOKE ALL ON FUNCTION nack_document(UUID, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nack_document(UUID, TEXT, TEXT, BOOLEAN) TO service_role;

REVOKE ALL ON FUNCTION renew_lease(UUID, TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION renew_lease(UUID, TEXT, INT) TO service_role;

-- ============================================
-- 完了
-- ============================================
COMMENT ON FUNCTION dequeue_document IS 'リース方式デキュー: 1件取得 + processing + リース設定を原子化';
COMMENT ON FUNCTION ack_document IS '処理完了: owner条件付きで completed に更新';
COMMENT ON FUNCTION nack_document IS '処理失敗: owner条件付きで pending/failed に更新';
COMMENT ON FUNCTION renew_lease IS 'リース延長: ロングジョブ用ハートビート';
