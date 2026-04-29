-- ============================================================
-- fast-indexer / 軽量インデックス用 pipeline_meta カラム
-- （PostgREST が未知カラムで 400 になると UI が黙って空になるため、
--   リポジトリのスキーマ定義に明示する）
-- ============================================================

ALTER TABLE public.pipeline_meta
  ADD COLUMN IF NOT EXISTS drive_file_id      TEXT,
  ADD COLUMN IF NOT EXISTS md_content         TEXT,
  ADD COLUMN IF NOT EXISTS text_embedded      BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS text_embedded_at   TIMESTAMPTZ;

COMMENT ON COLUMN public.pipeline_meta.drive_file_id IS
  'Google Drive ファイル ID（pipeline_meta 単位で保持する場合）';
COMMENT ON COLUMN public.pipeline_meta.md_content IS
  '軽量 fast-index 用の Markdown 本文（未設定時は 09 / raw から解決）';
COMMENT ON COLUMN public.pipeline_meta.text_embedded IS
  '軽量 fast-index で 10_ix_search_index への書き込み完了フラグ';

CREATE INDEX IF NOT EXISTS idx_pm_text_embedded_false
  ON public.pipeline_meta (raw_table, processing_status)
  WHERE text_embedded = FALSE;

CREATE INDEX IF NOT EXISTS idx_pm_drive_file_id
  ON public.pipeline_meta (drive_file_id)
  WHERE drive_file_id IS NOT NULL;

DO $$
BEGIN
  RAISE NOTICE 'pipeline_meta: fast-index 用カラム追加完了';
END $$;
