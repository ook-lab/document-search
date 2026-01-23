-- =============================================================================
-- Migration: Phase 5 - Execution Versioning（非破壊的処理）
-- =============================================================================
-- 目的:
-- 1. AI推論結果を上書きしない（再処理しても過去が残る）
-- 2. documents（実体）と executions（AI結果）を分離
-- 3. 失敗してもデータが残る（failed execution を保存、active は壊さない）
-- =============================================================================

BEGIN;

-- =============================================================================
-- STEP 1: document_executions テーブル作成
-- =============================================================================
-- ドキュメントごとの AI 推論実行履歴を保持
-- run_executions（ops_requests 用）とは別のテーブルとして作成

CREATE TABLE IF NOT EXISTS document_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- ドキュメントへの参照
    document_id UUID NOT NULL,  -- Rawdata_FILE_AND_MAIL.id への参照

    -- Phase 3/4 互換: owner_id 必須
    owner_id UUID NOT NULL,

    -- 実行状態
    status TEXT NOT NULL DEFAULT 'queued',

    -- モデル/プロンプト情報（再現性のため）
    model_version TEXT,          -- 使用したモデル名（例: gemini-2.5-flash）
    prompt_hash TEXT,            -- プロンプトのハッシュ（設定変更検知）

    -- 入力ハッシュ（同一入力検知・冪等性のため）
    input_hash TEXT NOT NULL,    -- 推論入力の SHA-256
    normalized_hash TEXT,        -- 前処理後テキストの SHA-256（将来拡張用）

    -- 実行系譜（リトライ追跡）
    retry_of_execution_id UUID,  -- リトライ元の execution_id（nullable）

    -- エラー情報
    error_code TEXT,             -- エラーコード（例: LLM_TIMEOUT, PARSE_ERROR）
    error_message TEXT,          -- エラーメッセージ

    -- AI 推論結果（JSONB で柔軟に保存）
    result_data JSONB DEFAULT '{}',  -- summary, metadata, chunks など

    -- パフォーマンス計測
    processing_duration_ms INTEGER,

    -- タイムスタンプ
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    -- 外部キー制約（Rawdata_FILE_AND_MAIL への参照）
    CONSTRAINT fk_document_executions_document
        FOREIGN KEY (document_id)
        REFERENCES "Rawdata_FILE_AND_MAIL"(id)
        ON DELETE CASCADE,

    -- 自己参照（リトライ系譜）
    CONSTRAINT fk_document_executions_retry
        FOREIGN KEY (retry_of_execution_id)
        REFERENCES document_executions(id)
        ON DELETE SET NULL
);

-- status の CHECK 制約（存在しない場合のみ追加）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'document_executions_status_check'
    ) THEN
        ALTER TABLE document_executions ADD CONSTRAINT document_executions_status_check
            CHECK (status IN (
                'queued',      -- キュー待ち
                'running',     -- 実行中
                'succeeded',   -- 成功
                'failed',      -- 失敗
                'canceled'     -- キャンセル
            ));
    END IF;
END $$;

-- =============================================================================
-- STEP 2: document_executions のインデックス
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_document_executions_document_id
    ON document_executions(document_id);
CREATE INDEX IF NOT EXISTS idx_document_executions_owner_id
    ON document_executions(owner_id);
CREATE INDEX IF NOT EXISTS idx_document_executions_status
    ON document_executions(status);
CREATE INDEX IF NOT EXISTS idx_document_executions_input_hash
    ON document_executions(input_hash);
CREATE INDEX IF NOT EXISTS idx_document_executions_created_at
    ON document_executions(created_at DESC);

-- 複合インデックス: 同一ドキュメントの最新成功 execution を高速に取得
CREATE INDEX IF NOT EXISTS idx_document_executions_doc_status_created
    ON document_executions(document_id, status, created_at DESC)
    WHERE status = 'succeeded';

-- =============================================================================
-- STEP 3: Rawdata_FILE_AND_MAIL に active_execution_id を追加
-- =============================================================================

ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS active_execution_id UUID;

-- 外部キー制約（document_executions への参照）
-- 注: 循環参照を避けるため DEFERRABLE にする
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_rawdata_active_execution'
    ) THEN
        ALTER TABLE "Rawdata_FILE_AND_MAIL"
        ADD CONSTRAINT fk_rawdata_active_execution
            FOREIGN KEY (active_execution_id)
            REFERENCES document_executions(id)
            ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

-- =============================================================================
-- STEP 4: RLS（Row Level Security）
-- =============================================================================

ALTER TABLE document_executions ENABLE ROW LEVEL SECURITY;

-- authenticated: 自分の owner_id の execution のみ読み取り可
DROP POLICY IF EXISTS "document_executions_select_own" ON document_executions;
CREATE POLICY "document_executions_select_own"
    ON document_executions FOR SELECT
    TO authenticated
    USING (owner_id = auth.uid());

-- service_role: 全操作可（Worker 用）
DROP POLICY IF EXISTS "document_executions_service_role_all" ON document_executions;
CREATE POLICY "document_executions_service_role_all"
    ON document_executions FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- anon: アクセス不可（Phase 4A の方針に従う）
-- （ポリシーなし = 暗黙の拒否）

-- =============================================================================
-- STEP 5: updated_at 自動更新トリガー
-- =============================================================================

CREATE OR REPLACE FUNCTION update_document_executions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();

    -- 終了状態に遷移したら completed_at を自動設定
    IF NEW.status IN ('succeeded', 'failed', 'canceled')
       AND OLD.status IN ('queued', 'running')
       AND NEW.completed_at IS NULL THEN
        NEW.completed_at = NOW();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_executions_updated_at ON document_executions;
CREATE TRIGGER trg_document_executions_updated_at
    BEFORE UPDATE ON document_executions
    FOR EACH ROW
    EXECUTE FUNCTION update_document_executions_updated_at();

-- =============================================================================
-- STEP 6: 便利なビュー
-- =============================================================================

-- ドキュメントと最新成功 execution の結合ビュー
CREATE OR REPLACE VIEW documents_with_active_execution AS
SELECT
    d.id AS document_id,
    d.file_name,
    d.doc_type,
    d.workspace,
    d.owner_id,
    d.active_execution_id,
    e.id AS execution_id,
    e.status AS execution_status,
    e.model_version,
    e.input_hash,
    e.result_data,
    e.created_at AS execution_created_at,
    e.processing_duration_ms
FROM "Rawdata_FILE_AND_MAIL" d
LEFT JOIN document_executions e ON d.active_execution_id = e.id;

COMMENT ON VIEW documents_with_active_execution IS 'ドキュメントと採用中の execution を結合';

-- ドキュメントの execution 履歴ビュー
CREATE OR REPLACE VIEW document_execution_history AS
SELECT
    d.id AS document_id,
    d.file_name,
    e.id AS execution_id,
    e.status,
    e.model_version,
    e.input_hash,
    e.retry_of_execution_id,
    e.error_code,
    e.error_message,
    e.created_at,
    e.completed_at,
    e.processing_duration_ms,
    CASE WHEN d.active_execution_id = e.id THEN true ELSE false END AS is_active
FROM "Rawdata_FILE_AND_MAIL" d
JOIN document_executions e ON e.document_id = d.id
ORDER BY d.id, e.created_at DESC;

COMMENT ON VIEW document_execution_history IS 'ドキュメントごとの execution 履歴（最新順）';

-- =============================================================================
-- STEP 7: コメント
-- =============================================================================

COMMENT ON TABLE document_executions IS 'Phase 5: ドキュメントごとの AI 推論実行履歴（非破壊）';
COMMENT ON COLUMN document_executions.document_id IS '処理対象ドキュメント（Rawdata_FILE_AND_MAIL.id）';
COMMENT ON COLUMN document_executions.owner_id IS 'データ所有者（Phase 3 互換）';
COMMENT ON COLUMN document_executions.input_hash IS '推論入力の SHA-256（同一入力検知）';
COMMENT ON COLUMN document_executions.normalized_hash IS '前処理後テキストの SHA-256（将来拡張用）';
COMMENT ON COLUMN document_executions.retry_of_execution_id IS 'リトライ元の execution（系譜追跡）';
COMMENT ON COLUMN document_executions.result_data IS 'AI 推論結果（summary, metadata, chunks など）';

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".active_execution_id IS '採用中の execution（succeeded のみ指す）';

COMMIT;

-- =============================================================================
-- 確認クエリ
-- =============================================================================
-- テーブル確認:
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'document_executions';
--
-- RLS 確認:
-- SELECT schemaname, tablename, policyname, cmd, roles
-- FROM pg_policies
-- WHERE tablename = 'document_executions';
