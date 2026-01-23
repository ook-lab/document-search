-- マイグレーション: worker_stateテーブルの作成
-- 作成日: 2026-01-16
-- 目的: ワーカー状態管理用テーブル（processing_lockの派生キャッシュ）
--
-- 注意: このテーブルは ops_requests / processing_lock の派生キャッシュです。
-- 主にレガシー互換性のために存在し、将来的に廃止予定（2025年Q2目標）です。
--
-- 実行方法: Supabase SQL Editor で実行してください

-- worker_state テーブル作成
CREATE TABLE IF NOT EXISTS worker_state (
    id SERIAL PRIMARY KEY,

    -- 処理状態
    is_processing BOOLEAN DEFAULT FALSE,
    stop_requested BOOLEAN DEFAULT FALSE,

    -- 進捗情報
    current_index INTEGER DEFAULT 0,
    total_count INTEGER DEFAULT 0,
    current_file TEXT DEFAULT '',
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    logs JSONB DEFAULT '[]'::jsonb,

    -- リソース情報
    cpu_percent REAL DEFAULT 0.0,
    memory_percent REAL DEFAULT 0.0,
    memory_used_gb REAL DEFAULT 0.0,
    memory_total_gb REAL DEFAULT 0.0,

    -- 制御情報
    throttle_delay REAL DEFAULT 0.0,
    adjustment_count INTEGER DEFAULT 0,
    max_parallel INTEGER DEFAULT 3,
    current_workers INTEGER DEFAULT 0,

    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 初期レコードを挿入（id=1 固定で1レコードのみ使用する設計）
INSERT INTO worker_state (id, is_processing, stop_requested)
VALUES (1, FALSE, FALSE)
ON CONFLICT (id) DO NOTHING;

-- RLSポリシー（必要に応じて有効化）
-- ALTER TABLE worker_state ENABLE ROW LEVEL SECURITY;

-- コメント
COMMENT ON TABLE worker_state IS 'ワーカー状態管理テーブル（processing_lock/ops_requestsの派生キャッシュ、将来廃止予定）';
COMMENT ON COLUMN worker_state.is_processing IS '処理中フラグ';
COMMENT ON COLUMN worker_state.stop_requested IS '停止要求フラグ（ops_requestsからの派生）';
COMMENT ON COLUMN worker_state.current_index IS '現在処理中のドキュメントのインデックス';
COMMENT ON COLUMN worker_state.total_count IS '処理対象ドキュメントの総数';
COMMENT ON COLUMN worker_state.current_file IS '現在処理中のファイル名';
COMMENT ON COLUMN worker_state.success_count IS '処理成功件数';
COMMENT ON COLUMN worker_state.error_count IS '処理エラー件数';
COMMENT ON COLUMN worker_state.logs IS '処理ログ（JSON配列）';
