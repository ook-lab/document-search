-- 実行ログ
CREATE TABLE IF NOT EXISTS ingestion_run_log (
  id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  source        TEXT NOT NULL,
  started_at    TIMESTAMPTZ DEFAULT NOW(),
  ended_at      TIMESTAMPTZ,
  status        TEXT,        -- 'running' | 'success' | 'error'
  log_output    TEXT,
  error_message TEXT
);

-- ソース別設定（非機密のみ）
CREATE TABLE IF NOT EXISTS ingestion_settings (
  source      TEXT PRIMARY KEY,
  settings    JSONB,         -- {person, extra_args, enabled, ...}
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);
