-- ============================================================
-- pipeline_meta にレビュー管理カラムを追加
-- Rawdata_FILE_AND_MAIL の review 関連カラムを移管
-- ============================================================

ALTER TABLE public.pipeline_meta
  ADD COLUMN IF NOT EXISTS review_status       TEXT    DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS latest_correction_id UUID,
  ADD COLUMN IF NOT EXISTS metadata            JSONB,
  ADD COLUMN IF NOT EXISTS gate_decision       TEXT,
  ADD COLUMN IF NOT EXISTS gate_block_code     TEXT,
  ADD COLUMN IF NOT EXISTS gate_block_reason   TEXT,
  ADD COLUMN IF NOT EXISTS gate_policy_version TEXT,
  ADD COLUMN IF NOT EXISTS reviewed_by         TEXT;

CREATE INDEX IF NOT EXISTS idx_pm_review_status      ON public.pipeline_meta (review_status);
CREATE INDEX IF NOT EXISTS idx_pm_latest_correction  ON public.pipeline_meta (latest_correction_id);

DO $$
BEGIN
  RAISE NOTICE 'pipeline_meta レビューカラム追加完了';
END $$;
