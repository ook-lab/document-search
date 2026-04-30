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
DO $$ BEGIN
  ALTER TABLE ai_model_pricing
    ADD CONSTRAINT ai_model_pricing_unique_key
    UNIQUE (model, source_type, prompt_tier);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 初期単価データ（モデルIDベース）。制約未整備のリモートでも落ちないよう NOT EXISTS。
-- gemini-2.5-flash-lite が二行ある場合は INSERT 順の先頭行を採用（従来 ON CONFLICT 時と同様）。
INSERT INTO ai_model_pricing (model, source_type, prompt_tier, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes)
SELECT model, source_type, prompt_tier, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes
FROM (
  SELECT DISTINCT ON (model, source_type, prompt_tier)
    model,
    source_type,
    prompt_tier,
    input_price_per_1m,
    output_price_per_1m,
    thinking_price_per_1m,
    notes
  FROM (VALUES
    (1, 'gemini-2.5-flash',            'all', 'all',      0.15::DECIMAL(12,6),   0.60::DECIMAL(12,6),  3.50::DECIMAL(12,6), 'Gemini 2.5 Flash'),
    (2, 'gemini-2.5-flash-lite',       'all', 'all',      0.075::DECIMAL(12,6),  0.30::DECIMAL(12,6),  0.0::DECIMAL(12,6),  'Gemini 2.5 Flash-Lite'),
    (3, 'gemini-2.5-pro',              'all', 'standard', 1.25::DECIMAL(12,6),  10.00::DECIMAL(12,6), 3.50::DECIMAL(12,6), 'Gemini 2.5 Pro (≤200K)'),
    (4, 'gemini-2.5-pro',              'all', 'large',    2.50::DECIMAL(12,6),  15.00::DECIMAL(12,6), 3.50::DECIMAL(12,6), 'Gemini 2.5 Pro (>200K)'),
    (5, 'gemini-2.5-flash-lite',       'all', 'all',      0.10::DECIMAL(12,6),   0.40::DECIMAL(12,6),  0.0::DECIMAL(12,6),  'Gemini 3.1 Flash-Lite'),
    (6, 'gemini-3-flash-preview',      'all', 'all',      0.10::DECIMAL(12,6),   0.40::DECIMAL(12,6),  0.0::DECIMAL(12,6),  'Gemini 3 Flash Preview'),
    (7, 'gemini-3.1-pro-preview',      'all', 'standard', 1.25::DECIMAL(12,6),  10.00::DECIMAL(12,6), 3.50::DECIMAL(12,6), 'Gemini 3.1 Pro Preview (≤200K)'),
    (8, 'gemini-3.1-pro-preview',      'all', 'large',    2.50::DECIMAL(12,6),  15.00::DECIMAL(12,6), 3.50::DECIMAL(12,6), 'Gemini 3.1 Pro Preview (>200K)'),
    (9, 'claude-sonnet-4-6',           'all', 'all',      3.00::DECIMAL(12,6),  15.00::DECIMAL(12,6), 0.0::DECIMAL(12,6),  'Claude Sonnet 4.6'),
    (10, 'gpt-4o',                      'all', 'all',      2.50::DECIMAL(12,6),  10.00::DECIMAL(12,6), 0.0::DECIMAL(12,6),  'GPT-4o'),
    (11, 'text-embedding-3-small',      'all', 'all',      0.02::DECIMAL(12,6),   0.0::DECIMAL(12,6),   0.0::DECIMAL(12,6),  'OpenAI Embedding')
  ) AS s(ord, model, source_type, prompt_tier, input_price_per_1m, output_price_per_1m, thinking_price_per_1m, notes)
  ORDER BY model, source_type, prompt_tier, ord
) AS v
WHERE NOT EXISTS (
  SELECT 1
  FROM ai_model_pricing e
  WHERE e.model = v.model
    AND e.source_type = v.source_type
    AND e.prompt_tier = v.prompt_tier
);
