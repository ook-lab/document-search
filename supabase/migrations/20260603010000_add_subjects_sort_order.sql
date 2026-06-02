-- Add sort_order column to public.quiz_subjects
ALTER TABLE public.quiz_subjects ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0 NOT NULL;

-- Backfill initial sort_order for seeded subjects
UPDATE public.quiz_subjects SET sort_order = 1 WHERE name = '国語';
UPDATE public.quiz_subjects SET sort_order = 2 WHERE name = '社会';
UPDATE public.quiz_subjects SET sort_order = 3 WHERE name = '理科';
UPDATE public.quiz_subjects SET sort_order = 4 WHERE name = '英語';
