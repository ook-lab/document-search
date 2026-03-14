-- ai_model_pricing に source_type / prompt_tier カラムを追加

ALTER TABLE ai_model_pricing
  ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'all',
  ADD COLUMN IF NOT EXISTS prompt_tier TEXT NOT NULL DEFAULT 'all';

-- 旧ユニーク制約を削除
DO $$ BEGIN
  ALTER TABLE ai_model_pricing DROP CONSTRAINT ai_model_pricing_model_effective_from_key;
EXCEPTION WHEN undefined_object THEN NULL; END $$;

-- 既存データの重複をクリーンアップ（gemini-2.5-flash 重複など）
DELETE FROM ai_model_pricing a
USING ai_model_pricing b
WHERE a.id > b.id
  AND a.model = b.model
  AND a.source_type = b.source_type
  AND a.prompt_tier = b.prompt_tier;

-- 新ユニーク制約: (model, source_type, prompt_tier) で一意
ALTER TABLE ai_model_pricing
  DROP CONSTRAINT IF EXISTS ai_model_pricing_unique_key;
ALTER TABLE ai_model_pricing
  ADD CONSTRAINT ai_model_pricing_unique_key
  UNIQUE (model, source_type, prompt_tier);

-- 初期単価データ（モデルIDベース）
INSERT INTO ai_model_pricing (model, source_type, prompt_tier, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes) VALUES
  ('gemini-2.5-flash',            'all', 'all',      0.15,   0.60,  3.50, 'Gemini 2.5 Flash'),
  ('gemini-2.5-flash-lite',       'all', 'all',      0.075,  0.30,  0.0,  'Gemini 2.5 Flash-Lite'),
  ('gemini-2.5-pro',              'all', 'standard', 1.25,  10.00,  3.50, 'Gemini 2.5 Pro (≤200K)'),
  ('gemini-2.5-pro',              'all', 'large',    2.50,  15.00,  3.50, 'Gemini 2.5 Pro (>200K)'),
  ('gemini-3.1-flash-lite-preview','all','all',      0.10,   0.40,  0.0,  'Gemini 3.1 Flash-Lite'),
  ('gemini-3-flash-preview',      'all', 'all',      0.10,   0.40,  0.0,  'Gemini 3 Flash Preview'),
  ('gemini-3.1-pro-preview',      'all', 'standard', 1.25,  10.00,  3.50, 'Gemini 3.1 Pro Preview (≤200K)'),
  ('gemini-3.1-pro-preview',      'all', 'large',    2.50,  15.00,  3.50, 'Gemini 3.1 Pro Preview (>200K)'),
  ('claude-sonnet-4-6',           'all', 'all',      3.00,  15.00,  0.0,  'Claude Sonnet 4.6'),
  ('gpt-4o',                      'all', 'all',      2.50,  10.00,  0.0,  'GPT-4o'),
  ('text-embedding-3-small',      'all', 'all',      0.02,   0.0,   0.0,  'OpenAI Embedding')
ON CONFLICT (model, source_type, prompt_tier) DO NOTHING;
