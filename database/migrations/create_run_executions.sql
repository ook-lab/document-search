-- ============================================================
-- run_executions テーブル: 処理実行のEvidence（証跡）
-- ============================================================
--
-- 【設計原則】
-- - ops_requests = 要求SSOT（意図のみ）
-- - run_executions = 実行Evidence（結果のみ）
-- - Worker は ops_requests を更新しない、run_executions のみ書き込む
-- - 同一 ops_request_id に対して複数回実行可能（リトライ対応）
--
-- 【冪等性】
-- - このSQLは何度実行しても同じ結果になる

-- ============================================================
-- テーブル作成（冪等）
-- ============================================================
CREATE TABLE IF NOT EXISTS run_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 元の要求（外部キー）
    ops_request_id UUID NOT NULL REFERENCES ops_requests(id),

    -- 実行状態
    status TEXT NOT NULL DEFAULT 'processing',

    -- Worker 情報
    worker_id TEXT,          -- 実行した Worker の識別子
    hostname TEXT,           -- 実行ホスト名
    pid INTEGER,             -- プロセスID

    -- 実行パラメータ（ops_requests.payload のスナップショット）
    executed_params JSONB DEFAULT '{}',

    -- 実行結果
    processed_count INTEGER DEFAULT 0,   -- 処理成功件数
    failed_count INTEGER DEFAULT 0,      -- 処理失敗件数
    skipped_count INTEGER DEFAULT 0,     -- スキップ件数
    error_message TEXT,                  -- エラーメッセージ（失敗時）
    error_details JSONB,                 -- 詳細エラー情報

    -- タイムスタンプ
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    -- 処理したドキュメントのID一覧（監査用）
    processed_doc_ids JSONB DEFAULT '[]'
);

-- ============================================================
-- CHECK 制約（冪等）
-- ============================================================

-- status の CHECK 制約
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'run_executions_status_check'
    ) THEN
        ALTER TABLE run_executions ADD CONSTRAINT run_executions_status_check
            CHECK (status IN (
                'processing',  -- 実行中
                'completed',   -- 正常完了
                'failed',      -- 失敗
                'cancelled'    -- キャンセル（STOPにより中断等）
            ));
    END IF;
END $$;

-- ============================================================
-- インデックス作成（冪等）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_run_executions_ops_request
    ON run_executions(ops_request_id);
CREATE INDEX IF NOT EXISTS idx_run_executions_status
    ON run_executions(status) WHERE status = 'processing';
CREATE INDEX IF NOT EXISTS idx_run_executions_started
    ON run_executions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_executions_worker
    ON run_executions(worker_id, started_at DESC);

-- ============================================================
-- RLS（Row Level Security）
-- ============================================================
ALTER TABLE run_executions ENABLE ROW LEVEL SECURITY;

-- 読み取りは全員可
DROP POLICY IF EXISTS "Enable read access for authenticated users" ON run_executions;
CREATE POLICY "Enable read access for authenticated users"
    ON run_executions FOR SELECT
    TO authenticated
    USING (true);

-- 書き込みは service_role のみ（Worker は service_role で接続）
DROP POLICY IF EXISTS "Enable insert for service role" ON run_executions;
CREATE POLICY "Enable insert for service role"
    ON run_executions FOR INSERT
    TO service_role
    WITH CHECK (true);

DROP POLICY IF EXISTS "Enable update for service role" ON run_executions;
CREATE POLICY "Enable update for service role"
    ON run_executions FOR UPDATE
    TO service_role
    USING (true);

-- ============================================================
-- コメント（冪等）
-- ============================================================
COMMENT ON TABLE run_executions IS '処理実行のEvidence - Workerのみが書き込み、ops_requestsとは分離';
COMMENT ON COLUMN run_executions.ops_request_id IS '元の RUN 要求ID（同一IDに対して複数実行可能）';
COMMENT ON COLUMN run_executions.status IS '実行状態: processing->completed/failed/cancelled';
COMMENT ON COLUMN run_executions.worker_id IS 'Worker識別子（hostname:pid:timestamp形式推奨）';
COMMENT ON COLUMN run_executions.executed_params IS '実行時のパラメータスナップショット';
COMMENT ON COLUMN run_executions.processed_doc_ids IS '処理したドキュメントIDの配列（監査用）';

-- ============================================================
-- 便利なビュー: 直近の実行結果（冪等）
-- ============================================================
CREATE OR REPLACE VIEW recent_run_executions AS
SELECT
    re.id,
    re.ops_request_id,
    re.status,
    re.worker_id,
    re.processed_count,
    re.failed_count,
    re.skipped_count,
    re.error_message,
    re.started_at,
    re.completed_at,
    re.completed_at - re.started_at as duration,
    opr.payload as request_payload
FROM run_executions re
JOIN ops_requests opr ON re.ops_request_id = opr.id
ORDER BY re.started_at DESC
LIMIT 100;

COMMENT ON VIEW recent_run_executions IS '直近100件の実行結果';

-- ============================================================
-- 便利なビュー: RUN 要求と実行状況の結合（冪等）
-- ============================================================
CREATE OR REPLACE VIEW run_requests_with_executions AS
SELECT
    opr.id as request_id,
    opr.payload,
    opr.status as request_status,
    opr.requested_by,
    opr.created_at as requested_at,
    COALESCE(
        (SELECT json_agg(json_build_object(
            'execution_id', re.id,
            'status', re.status,
            'processed_count', re.processed_count,
            'failed_count', re.failed_count,
            'started_at', re.started_at,
            'completed_at', re.completed_at
        ) ORDER BY re.started_at DESC)
        FROM run_executions re
        WHERE re.ops_request_id = opr.id),
        '[]'::json
    ) as executions,
    (SELECT COUNT(*) FROM run_executions re WHERE re.ops_request_id = opr.id) as execution_count
FROM ops_requests opr
WHERE opr.request_type = 'RUN'
ORDER BY opr.created_at DESC;

COMMENT ON VIEW run_requests_with_executions IS 'RUN要求と実行履歴の結合ビュー';

-- ============================================================
-- トリガー: 実行完了時に completed_at を自動設定（冪等）
-- ============================================================
CREATE OR REPLACE FUNCTION set_run_execution_completed_at()
RETURNS TRIGGER AS $$
BEGIN
    -- 終了状態に遷移したら completed_at を自動設定
    IF NEW.status IN ('completed', 'failed', 'cancelled')
       AND OLD.status = 'processing'
       AND NEW.completed_at IS NULL THEN
        NEW.completed_at := NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION set_run_execution_completed_at() IS '実行完了時にcompleted_atを自動設定';

DROP TRIGGER IF EXISTS trg_run_executions_completed_at ON run_executions;
CREATE TRIGGER trg_run_executions_completed_at
    BEFORE UPDATE ON run_executions
    FOR EACH ROW
    EXECUTE FUNCTION set_run_execution_completed_at();

-- ============================================================
-- 重複実行方針のドキュメント
-- ============================================================
-- 【方針】同一 ops_request_id に対して複数回実行を許可
--
-- 理由:
-- 1. リトライ対応: 失敗した実行を再試行できる
-- 2. 増分処理: max_items=5 で複数回実行して全件処理できる
-- 3. 監査: 全実行履歴が残る
--
-- 使い方:
-- - 1回目実行: Worker --run-request <id> --execute
-- - 2回目実行: 同じコマンドで再実行可能
-- - 各実行は run_executions に別レコードとして記録
--
-- 注意:
-- - ops_requests.status は ops.py のみが更新
-- - Worker は run_executions のみ書き込む

-- ============================================================
-- マイグレーション履歴を記録（冪等）
-- ============================================================
INSERT INTO schema_migrations (filename, applied_by, notes)
VALUES (
    'create_run_executions.sql',
    current_user,
    'run_executions テーブル、CHECK制約、ビュー、completed_atトリガー'
)
ON CONFLICT (filename) DO UPDATE SET
    applied_at = NOW(),
    notes = EXCLUDED.notes;

-- ============================================================
-- 適用完了メッセージ
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE '✅ create_run_executions.sql 適用完了';
    RAISE NOTICE '  - run_executions テーブル';
    RAISE NOTICE '  - status CHECK 制約 (processing/completed/failed/cancelled)';
    RAISE NOTICE '  - completed_at 自動設定トリガー';
    RAISE NOTICE '  - recent_run_executions ビュー';
    RAISE NOTICE '  - run_requests_with_executions ビュー';
    RAISE NOTICE '  - 重複実行方針: 同一要求に複数回実行を許可';
END $$;
