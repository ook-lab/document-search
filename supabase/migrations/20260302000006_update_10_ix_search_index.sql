-- ============================================================
-- 10_ix_search_index にチャンクメタ追加
-- MetadataChunker の chunk_type / chunk_weight を保持
-- ============================================================

ALTER TABLE public."10_ix_search_index"
  ADD COLUMN IF NOT EXISTS chunk_type   TEXT,
  ADD COLUMN IF NOT EXISTS chunk_weight FLOAT DEFAULT 1.0;

DO $$
BEGIN
  RAISE NOTICE '10_ix_search_index: chunk_type / chunk_weight 追加完了';
END $$;
