-- math_problems テーブルに grading_status カラムを追加
ALTER TABLE public.math_problems
  ADD COLUMN IF NOT EXISTS grading_status JSONB DEFAULT '{}'::jsonb;
