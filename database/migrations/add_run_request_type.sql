-- ============================================================
-- RUN request_type 追加マイグレーション
-- ============================================================
--
-- 【目的】
-- - ops_requests に RUN (処理実行要求) を追加
-- - WebからWorker処理を要求できるようにする
--
-- 【設計原則】
-- - ops_requests = 要求SSOT（実行結果は別テーブル）
-- - Worker は ops_requests を更新しない
-- - apply は ops.py のみが行う
--
-- 【冪等性】
-- - このSQLは何度実行しても同じ結果になる

-- ============================================================
-- request_type CHECK 制約を更新（RUN 追加）
-- ============================================================
-- 既存制約を削除して再作成（冪等）
DO $$
BEGIN
    -- 既存の制約があれば削除
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ops_requests_request_type_check'
    ) THEN
        ALTER TABLE ops_requests DROP CONSTRAINT ops_requests_request_type_check;
    END IF;

    -- 新しい制約を追加（RUN を含む）
    ALTER TABLE ops_requests ADD CONSTRAINT ops_requests_request_type_check
        CHECK (request_type IN (
            'STOP',              -- 処理停止
            'RESUME',            -- 処理再開
            'RELEASE_LEASE',     -- リース解放（stuck対策）
            'RESET_DOC',         -- 単一ドキュメントをpendingに戻す
            'RESET_WORKSPACE',   -- workspace全体をpendingに戻す
            'CLEAR_STAGES',      -- ステージE-Kをクリア
            'PAUSE',             -- 一時停止（新規処理を抑止）
            'CANCEL',            -- この要求をキャンセル
            'RUN'                -- 処理実行要求（NEW）
        ));
END $$;

-- ============================================================
-- RUN 要求用のインデックス（冪等）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_ops_requests_run_queued
    ON ops_requests(created_at DESC)
    WHERE request_type = 'RUN' AND status = 'queued';

-- ============================================================
-- コメント更新（冪等）
-- ============================================================
COMMENT ON COLUMN ops_requests.request_type IS
    '要求の種類: STOP, RESUME, RELEASE_LEASE, RESET_DOC, RESET_WORKSPACE, CLEAR_STAGES, PAUSE, CANCEL, RUN';

-- ============================================================
-- RUN 用 payload スキーマのドキュメント
-- ============================================================
-- RUN request_type の payload には以下を格納:
-- {
--   "max_items": 5,              -- 最大処理件数（デフォルト5）
--   "workspace": "ema_classroom", -- 対象ワークスペース（省略時は全体）
--   "doc_id": "uuid",            -- 特定ドキュメントのみ（省略時は自動選択）
--   "priority": "normal"         -- 優先度: normal, high, low
-- }
--
-- 【重要】
-- - payload は要求の「意図」のみを格納
-- - 実行結果（処理件数、エラー等）は run_executions テーブルに格納
-- - Worker は ops_requests を更新しない（run_executions のみ書き込む）

-- ============================================================
-- マイグレーション履歴を記録（冪等）
-- ============================================================
INSERT INTO schema_migrations (filename, applied_by, notes)
VALUES (
    'add_run_request_type.sql',
    current_user,
    'RUN request_type 追加、インデックス追加'
)
ON CONFLICT (filename) DO UPDATE SET
    applied_at = NOW(),
    notes = EXCLUDED.notes;

-- ============================================================
-- 適用完了メッセージ
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE '✅ add_run_request_type.sql 適用完了';
    RAISE NOTICE '  - request_type CHECK 制約に RUN 追加';
    RAISE NOTICE '  - idx_ops_requests_run_queued インデックス追加';
END $$;
