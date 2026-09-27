-- 20260928000001_pipeline_batch.sql
-- Gemini Batch API を用いたパイプライン一括直接抽出用のテーブル定義とメタ列追加（削除系文なし）

-- 1. 09_unified_documents_meta にパイプラインエラー記録列を追加
ALTER TABLE "09_unified_documents_meta"
    ADD COLUMN IF NOT EXISTS ix_pipeline_error text,
    ADD COLUMN IF NOT EXISTS ix_pipeline_error_at timestamptz;

-- 2. pipeline_batch_files: ファイル単位のバッチジョブ状態管理
CREATE TABLE IF NOT EXISTS pipeline_batch_files (
    raw_table text NOT NULL,
    raw_id text NOT NULL,
    drive_file_id text NOT NULL,
    total_pages int NOT NULL,
    job_name text NOT NULL,
    state text NOT NULL,  -- submitted / completed / error
    submitted_at timestamptz NOT NULL,
    finished_at timestamptz,
    error text,
    PRIMARY KEY (raw_table, raw_id)
);

-- 3. pipeline_batch_pages: ページ単位の抽出結果（Markdown & ブロック JSONB）
CREATE TABLE IF NOT EXISTS pipeline_batch_pages (
    raw_table text NOT NULL,
    raw_id text NOT NULL,
    page_index int NOT NULL,
    markdown text NOT NULL,
    blocks jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (raw_table, raw_id, page_index)
);

-- 検索・照会用インデックス
CREATE INDEX IF NOT EXISTS idx_pipeline_batch_files_state ON pipeline_batch_files(state);
CREATE INDEX IF NOT EXISTS idx_pipeline_batch_files_submitted_at ON pipeline_batch_files(submitted_at);
