-- math_problems テーブルのスキーマ変更
-- 1. school_name を source_book (教材名) にリネーム
ALTER TABLE public.math_problems
  RENAME COLUMN school_name TO source_book;

-- 2. year (INTEGER) を chapter (TEXT, 章) に変更
ALTER TABLE public.math_problems
  ALTER COLUMN year TYPE TEXT USING year::text;

ALTER TABLE public.math_problems
  RENAME COLUMN year TO chapter;

-- 3. category を unit (単元) にリネーム
ALTER TABLE public.math_problems
  RENAME COLUMN category TO unit;

-- 4. 不要なカラム (sub_category, difficulty) の削除
ALTER TABLE public.math_problems
  DROP COLUMN IF EXISTS sub_category;

ALTER TABLE public.math_problems
  DROP COLUMN IF EXISTS difficulty;
