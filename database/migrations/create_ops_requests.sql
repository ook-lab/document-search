-- ============================================================
-- ops_requests テーブル: 運用操作要求のSSOT
-- ============================================================
--
-- 【設計原則】
-- - WebはこのテーブルにINSERTするだけ（要求をenqueue）
-- - Worker/Opsがこれをleaseして適用
-- - 停止/リセット/リース解放の「真実」はこのテーブルのみ
-- - 分散ガードを防ぎ、運用操作の一元管理を実現
--
-- 【冪等性】
-- - このSQLは何度実行しても同じ結果になる
-- - 途中で失敗しても再実行可能
-- - IF NOT EXISTS / OR REPLACE / DROP ... IF EXISTS を使用

-- ============================================================
-- テーブル作成（冪等）
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 要求の種類
    request_type TEXT NOT NULL,

    -- スコープ（対象）
    scope_type TEXT,
    scope_id TEXT,  -- workspace名 または rawdata_id

    -- 追加情報（JSON）
    payload JSONB DEFAULT '{}',

    -- 状態
    status TEXT NOT NULL DEFAULT 'queued',

    -- 監査情報
    requested_by TEXT,  -- 要求元（UI, API, ops.py等）
    applied_by TEXT,    -- 適用者（worker, ops.py等）

    -- 結果情報
    result_message TEXT,     -- 適用結果メッセージ
    affected_count INTEGER,  -- 影響を受けた件数

    -- タイムスタンプ
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,  -- 有効期限（nullなら無期限）

    -- dry-run サポート
    is_dry_run BOOLEAN DEFAULT FALSE,
    dry_run_result JSONB  -- dry-run実行時の予測結果
);

-- ============================================================
-- CHECK 制約の追加（冪等 - 重複時は無視）
-- ============================================================

-- request_type の CHECK 制約
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ops_requests_request_type_check'
    ) THEN
        ALTER TABLE ops_requests ADD CONSTRAINT ops_requests_request_type_check
            CHECK (request_type IN (
                'STOP',              -- 処理停止
                'RESUME',            -- 処理再開
                'RELEASE_LEASE',     -- リース解放（stuck対策）
                'RESET_DOC',         -- 単一ドキュメントをpendingに戻す
                'RESET_WORKSPACE',   -- workspace全体をpendingに戻す
                'CLEAR_STAGES',      -- ステージE-Kをクリア
                'PAUSE',             -- 一時停止（新規処理を抑止）
                'CANCEL'             -- この要求をキャンセル
            ));
    END IF;
END $$;

-- status の CHECK 制約
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ops_requests_status_check'
    ) THEN
        ALTER TABLE ops_requests ADD CONSTRAINT ops_requests_status_check
            CHECK (status IN (
                'queued',     -- 待機中
                'applied',    -- 適用済み
                'rejected',   -- 拒否（条件不一致等）
                'cancelled'   -- キャンセル済み
            ));
    END IF;
END $$;

-- scope_type の CHECK 制約
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ops_requests_scope_type_check'
    ) THEN
        ALTER TABLE ops_requests ADD CONSTRAINT ops_requests_scope_type_check
            CHECK (scope_type IN ('workspace', 'document', 'global'));
    END IF;
END $$;

-- ============================================================
-- インデックス作成（冪等）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_ops_requests_status
    ON ops_requests(status) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_ops_requests_type
    ON ops_requests(request_type);
CREATE INDEX IF NOT EXISTS idx_ops_requests_scope
    ON ops_requests(scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_ops_requests_created
    ON ops_requests(created_at DESC);

-- ============================================================
-- RLS（Row Level Security）
-- ============================================================
ALTER TABLE ops_requests ENABLE ROW LEVEL SECURITY;

-- ポリシーの作成（冪等 - 既存なら削除して再作成）
DROP POLICY IF EXISTS "Enable read access for authenticated users" ON ops_requests;
CREATE POLICY "Enable read access for authenticated users"
    ON ops_requests FOR SELECT
    TO authenticated
    USING (true);

DROP POLICY IF EXISTS "Enable insert for authenticated users" ON ops_requests;
CREATE POLICY "Enable insert for authenticated users"
    ON ops_requests FOR INSERT
    TO authenticated
    WITH CHECK (true);

DROP POLICY IF EXISTS "Enable update for service role" ON ops_requests;
CREATE POLICY "Enable update for service role"
    ON ops_requests FOR UPDATE
    TO service_role
    USING (true);

-- ============================================================
-- コメント（冪等）
-- ============================================================
COMMENT ON TABLE ops_requests IS '運用操作要求のSSOT - Webはenqueue、Worker/Opsが適用';
COMMENT ON COLUMN ops_requests.request_type IS '要求の種類: STOP, RESUME, RELEASE_LEASE, RESET_DOC, RESET_WORKSPACE, CLEAR_STAGES, PAUSE, CANCEL';
COMMENT ON COLUMN ops_requests.scope_type IS '対象の種類: workspace, document, global';
COMMENT ON COLUMN ops_requests.scope_id IS '対象ID: workspace名またはrawdata_id';
COMMENT ON COLUMN ops_requests.status IS '状態: queued->applied/rejected/cancelled';
COMMENT ON COLUMN ops_requests.is_dry_run IS 'dry-runモード: trueなら実行せず影響予測のみ';

-- ============================================================
-- 便利なビュー: 直近の運用要求（冪等）
-- ============================================================
CREATE OR REPLACE VIEW recent_ops_requests AS
SELECT
    id,
    request_type,
    scope_type,
    scope_id,
    status,
    requested_by,
    affected_count,
    result_message,
    created_at,
    applied_at
FROM ops_requests
ORDER BY created_at DESC
LIMIT 100;

COMMENT ON VIEW recent_ops_requests IS '直近100件の運用要求';

-- ============================================================
-- 関数: 未処理の停止要求があるか確認（冪等）
-- ============================================================
CREATE OR REPLACE FUNCTION has_pending_stop_request()
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM ops_requests
        WHERE request_type IN ('STOP', 'PAUSE')
        AND status = 'queued'
        AND (expires_at IS NULL OR expires_at > NOW())
    );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION has_pending_stop_request() IS '未処理の停止/一時停止要求があるか確認';

-- ============================================================
-- 関数: スコープ付き停止要求があるか確認（冪等）
-- ============================================================
CREATE OR REPLACE FUNCTION has_pending_stop_request_for_scope(
    p_scope_type TEXT,
    p_scope_id TEXT DEFAULT NULL
)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM ops_requests
        WHERE request_type IN ('STOP', 'PAUSE')
        AND status = 'queued'
        AND (expires_at IS NULL OR expires_at > NOW())
        AND scope_type = p_scope_type
        AND (p_scope_id IS NULL OR scope_id = p_scope_id)
    );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION has_pending_stop_request_for_scope(TEXT, TEXT) IS 'スコープ付き停止要求があるか確認';

-- ============================================================
-- トリガー関数: 状態遷移制約（冪等）
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_ops_requests_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    -- 終了状態から queued への逆戻りを禁止
    IF OLD.status IN ('applied', 'rejected', 'cancelled') AND NEW.status = 'queued' THEN
        RAISE EXCEPTION 'Invalid status transition: % -> queued is not allowed', OLD.status;
    END IF;

    -- applied_at を自動設定
    IF NEW.status IN ('applied', 'rejected', 'cancelled') AND OLD.status = 'queued' THEN
        NEW.applied_at := NOW();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION enforce_ops_requests_status_transition() IS '状態遷移制約: 終了状態からqueuedへの逆戻り禁止';

-- トリガー作成（冪等 - 削除して再作成）
DROP TRIGGER IF EXISTS trg_ops_requests_status_transition ON ops_requests;
CREATE TRIGGER trg_ops_requests_status_transition
    BEFORE UPDATE ON ops_requests
    FOR EACH ROW
    EXECUTE FUNCTION enforce_ops_requests_status_transition();

-- ============================================================
-- マイグレーション履歴テーブル（冪等）
-- ============================================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by TEXT,
    git_sha TEXT,
    notes TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_schema_migrations_filename
    ON schema_migrations(filename);

-- このマイグレーションの履歴を記録（冪等 - ON CONFLICT で更新）
INSERT INTO schema_migrations (filename, applied_by, notes)
VALUES (
    'create_ops_requests.sql',
    current_user,
    'ops_requests テーブル、CHECK制約、状態遷移トリガー、スコープ付き停止確認関数'
)
ON CONFLICT (filename) DO UPDATE SET
    applied_at = NOW(),
    notes = EXCLUDED.notes;

-- ============================================================
-- 適用完了メッセージ
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE '✅ create_ops_requests.sql 適用完了';
    RAISE NOTICE '  - ops_requests テーブル';
    RAISE NOTICE '  - CHECK 制約 (request_type, status, scope_type)';
    RAISE NOTICE '  - 状態遷移トリガー (applied->queued 禁止)';
    RAISE NOTICE '  - has_pending_stop_request 関数';
    RAISE NOTICE '  - has_pending_stop_request_for_scope 関数';
    RAISE NOTICE '  - schema_migrations 履歴';
END $$;
