-- Run this in Supabase SQL Editor
-- calendar_groups テーブルを新しい calendar_configs 構造に移行する

-- 1. 新カラム追加
ALTER TABLE calendar_groups ADD COLUMN IF NOT EXISTS calendar_configs JSONB DEFAULT '[]'::jsonb;

-- 2. 既存の base_ids データを calendar_configs に変換（viewType は "both" として移行）
UPDATE calendar_groups
SET calendar_configs = (
  SELECT jsonb_agg(jsonb_build_object('calendarId', elem, 'viewType', 'both'))
  FROM unnest(base_ids) AS elem
)
WHERE base_ids IS NOT NULL AND array_length(base_ids, 1) > 0;

-- 3. 旧カラム削除
ALTER TABLE calendar_groups DROP COLUMN IF EXISTS base_ids;
