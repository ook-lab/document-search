-- ============================================================
-- 10_ix_search_index: chunk_metadata / search_weight カラム追加
-- ============================================================
-- Stage K が chunk_metadata と search_weight を INSERT しているが、
-- カラムが存在しない場合 Stage K の INSERT が全件失敗する。
-- IF NOT EXISTS なので適用済み環境に再適用しても安全。
-- ============================================================

ALTER TABLE "10_ix_search_index"
    ADD COLUMN IF NOT EXISTS chunk_metadata JSONB,
    ADD COLUMN IF NOT EXISTS search_weight  FLOAT DEFAULT 1.0;

-- chunk_metadata への GIN インデックス（検索高速化）
CREATE INDEX IF NOT EXISTS idx_search_index_chunk_metadata_gin
ON "10_ix_search_index" USING gin (chunk_metadata jsonb_path_ops);

DO $$
BEGIN
    RAISE NOTICE '✅ 20260227000004_add_chunk_metadata.sql 適用完了';
    RAISE NOTICE '  - 10_ix_search_index: chunk_metadata JSONB 追加';
    RAISE NOTICE '  - 10_ix_search_index: search_weight FLOAT DEFAULT 1.0 追加';
    RAISE NOTICE '  - GIN インデックス idx_search_index_chunk_metadata_gin 追加';
END $$;
