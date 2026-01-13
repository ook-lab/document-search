-- マイグレーション: processing_lockテーブルにリアルタイム進捗表示用カラム追加
-- 作成日: 2026-01-11
-- 目的: 処理状況のリアルタイム表示に必要なカラムを追加

-- 進捗情報カラム
ALTER TABLE processing_lock
ADD COLUMN IF NOT EXISTS current_index INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS total_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS current_file TEXT DEFAULT '',
ADD COLUMN IF NOT EXISTS success_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS error_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS logs JSONB DEFAULT '[]'::jsonb;

-- リソース情報カラム
ALTER TABLE processing_lock
ADD COLUMN IF NOT EXISTS cpu_percent REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS memory_percent REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS memory_used_gb REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS memory_total_gb REAL DEFAULT 0.0;

-- 制御情報カラム
ALTER TABLE processing_lock
ADD COLUMN IF NOT EXISTS throttle_delay REAL DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS adjustment_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS max_parallel INTEGER DEFAULT 3,
ADD COLUMN IF NOT EXISTS current_workers INTEGER DEFAULT 0;

-- コメント追加
COMMENT ON COLUMN processing_lock.current_index IS '現在処理中のドキュメントのインデックス（0始まり）';
COMMENT ON COLUMN processing_lock.total_count IS '処理対象ドキュメントの総数';
COMMENT ON COLUMN processing_lock.current_file IS '現在処理中のファイル名';
COMMENT ON COLUMN processing_lock.success_count IS '処理成功件数';
COMMENT ON COLUMN processing_lock.error_count IS '処理エラー件数';
COMMENT ON COLUMN processing_lock.logs IS '処理ログ（JSON配列）';

COMMENT ON COLUMN processing_lock.cpu_percent IS 'CPU使用率（%）';
COMMENT ON COLUMN processing_lock.memory_percent IS 'メモリ使用率（%）';
COMMENT ON COLUMN processing_lock.memory_used_gb IS 'メモリ使用量（GB）';
COMMENT ON COLUMN processing_lock.memory_total_gb IS '総メモリ容量（GB）';

COMMENT ON COLUMN processing_lock.throttle_delay IS 'スロットル遅延（秒）';
COMMENT ON COLUMN processing_lock.adjustment_count IS 'リソース調整回数';
COMMENT ON COLUMN processing_lock.max_parallel IS '最大並列処理数';
COMMENT ON COLUMN processing_lock.current_workers IS '現在のワーカー数';
