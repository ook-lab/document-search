-- 運用上の用語整理: 誤解を招くインデックス名と COMMENT を置き換える（データ列は変更しない）。

ALTER INDEX IF EXISTS public.idx_pm_fast_index_unvectorized
  RENAME TO idx_pm_search_vectorize_pending;

COMMENT ON COLUMN public.pipeline_meta.vectorized_at IS
  '10_ix_search_index へのベクトル化書き込みが完了した時刻（rag-prepare による登録を含む）。';

COMMENT ON COLUMN public."09_unified_documents_meta".ix_vectorized_at IS
  'rag-prepare が 10_ix_search_index を更新した日時。';
