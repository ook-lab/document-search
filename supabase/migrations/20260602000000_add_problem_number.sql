-- math_problems テーブルに problem_number (問題番号) カラムを追加
ALTER TABLE public.math_problems
  ADD COLUMN IF NOT EXISTS problem_number TEXT;
