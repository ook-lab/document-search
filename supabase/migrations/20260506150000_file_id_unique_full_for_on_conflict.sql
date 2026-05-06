-- ============================================================
-- file_id: ON CONFLICT (file_id) 用に「非部分」の一意インデックスへ
-- ============================================================
-- 部分一意 (WHERE file_id IS NOT NULL) だと PostgREST の
-- ?on_conflict=file_id + resolution=ignore-duplicates が 42P10 になる。
-- PostgreSQL では UNIQUE 列に複数 NULL を入れてよい（NULL は互いに非等価）。

DROP INDEX IF EXISTS public.uq_04_ikuya_classroom_file_id;
CREATE UNIQUE INDEX uq_04_ikuya_classroom_file_id
  ON public."04_ikuya_classroom_01_raw"(file_id);

DROP INDEX IF EXISTS public.uq_03_ema_classroom_file_id;
CREATE UNIQUE INDEX uq_03_ema_classroom_file_id
  ON public."03_ema_classroom_01_raw"(file_id);
