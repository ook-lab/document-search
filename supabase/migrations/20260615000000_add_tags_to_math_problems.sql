-- math_problems テーブルに tags カラムを追加
ALTER TABLE public.math_problems
  ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';
