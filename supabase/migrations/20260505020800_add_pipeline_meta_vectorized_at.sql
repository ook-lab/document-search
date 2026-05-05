-- ============================================================
-- fast-index: ベクトル化済みを processing_status から分離する
-- ============================================================

ALTER TABLE public.pipeline_meta
  ADD COLUMN IF NOT EXISTS vectorized_at TIMESTAMPTZ;

COMMENT ON COLUMN public.pipeline_meta.vectorized_at IS
  'fast-index で 10_ix_search_index へのベクトル化書き込みが完了した時刻';

CREATE INDEX IF NOT EXISTS idx_pm_fast_index_unvectorized
  ON public.pipeline_meta (raw_table, updated_at DESC, created_at DESC)
  WHERE vectorized_at IS NULL;

DO $$
BEGIN
  RAISE NOTICE 'pipeline_meta: vectorized_at を追加しました';
END $$;
