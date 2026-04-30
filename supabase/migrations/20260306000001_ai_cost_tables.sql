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

-- リモートで CREATE TABLE がスキップされ、(model, effective_from) の UNIQUE が無い場合でも
-- ON CONFLICT が落ちないよう、モデル単位で未登録ならだけ投入する。
INSERT INTO ai_model_pricing (model, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes)
SELECT model, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes
FROM (VALUES
    ('gemini-2.5-flash',       0.15::DECIMAL(12,6),   0.60::DECIMAL(12,6),  3.50::DECIMAL(12,6), 'Gemini 2.5 Flash'),
    ('gemini-2.5-flash-lite',  0.075::DECIMAL(12,6),  0.30::DECIMAL(12,6),  0.0::DECIMAL(12,6),  'Gemini 2.5 Flash-Lite'),
    ('gemini-2.5-pro',         1.25::DECIMAL(12,6),  10.00::DECIMAL(12,6), 3.50::DECIMAL(12,6), 'Gemini 2.5 Pro'),
    ('claude-sonnet-4-6',      3.00::DECIMAL(12,6),  15.00::DECIMAL(12,6), 0.0::DECIMAL(12,6),  'Claude Sonnet 4.6'),
    ('gpt-4o',                 2.50::DECIMAL(12,6),  10.00::DECIMAL(12,6), 0.0::DECIMAL(12,6),  'GPT-4o'),
    ('text-embedding-3-small', 0.02::DECIMAL(12,6),   0.0::DECIMAL(12,6),   0.0::DECIMAL(12,6),  'OpenAI Embedding')
) AS v(model, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes)
WHERE NOT EXISTS (SELECT 1 FROM ai_model_pricing e WHERE e.model = v.model);
