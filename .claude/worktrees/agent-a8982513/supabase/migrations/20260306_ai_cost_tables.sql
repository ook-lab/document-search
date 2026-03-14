-- AI使用量ログテーブル
CREATE TABLE IF NOT EXISTS ai_usage_logs (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at             TIMESTAMPTZ DEFAULT NOW(),
    app                    TEXT NOT NULL,
    stage                  TEXT,
    model                  TEXT NOT NULL,
    session_id             UUID,
    workspace_id           UUID,
    prompt_token_count     INTEGER DEFAULT 0,
    candidates_token_count INTEGER DEFAULT 0,
    thoughts_token_count   INTEGER DEFAULT 0,
    total_token_count      INTEGER DEFAULT 0,
    metadata               JSONB
);
CREATE INDEX IF NOT EXISTS ai_usage_logs_created_at_idx ON ai_usage_logs(created_at);
CREATE INDEX IF NOT EXISTS ai_usage_logs_app_idx        ON ai_usage_logs(app);
CREATE INDEX IF NOT EXISTS ai_usage_logs_stage_idx      ON ai_usage_logs(stage);
CREATE INDEX IF NOT EXISTS ai_usage_logs_model_idx      ON ai_usage_logs(model);

-- AIモデル単価マスタ
CREATE TABLE IF NOT EXISTS ai_model_pricing (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model                   TEXT NOT NULL,
    input_price_per_1m      DECIMAL(12,6) DEFAULT 0,
    output_price_per_1m     DECIMAL(12,6) DEFAULT 0,
    thinking_price_per_1m   DECIMAL(12,6) DEFAULT 0,
    effective_from          TIMESTAMPTZ DEFAULT NOW(),
    currency                TEXT DEFAULT 'USD',
    notes                   TEXT,
    UNIQUE(model, effective_from)
);

INSERT INTO ai_model_pricing (model, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes) VALUES
('gemini-2.5-flash',       0.15,   0.60,  3.50, 'Gemini 2.5 Flash'),
('gemini-2.5-flash-lite',  0.075,  0.30,  0.0,  'Gemini 2.5 Flash-Lite'),
('gemini-2.5-pro',         1.25,  10.00,  3.50, 'Gemini 2.5 Pro'),
('claude-sonnet-4-6',      3.00,  15.00,  0.0,  'Claude Sonnet 4.6'),
('gpt-4o',                 2.50,  10.00,  0.0,  'GPT-4o'),
('text-embedding-3-small', 0.02,   0.0,   0.0,  'OpenAI Embedding')
ON CONFLICT (model, effective_from) DO NOTHING;
