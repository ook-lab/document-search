-- 10_ix_search_index は常に 09_unified_documents の下請け。
-- doc_id が 09 に存在しない行は許されない（09 を迂回した 10 だけの経路を封じる）。

DELETE FROM public."10_ix_search_index" t
WHERE NOT EXISTS (
  SELECT 1 FROM public."09_unified_documents" u WHERE u.id = t.doc_id
);

ALTER TABLE public."10_ix_search_index"
  DROP CONSTRAINT IF EXISTS fk_10_ix_search_index_doc_id_09_unified_documents;

ALTER TABLE public."10_ix_search_index"
  ADD CONSTRAINT fk_10_ix_search_index_doc_id_09_unified_documents
  FOREIGN KEY (doc_id)
  REFERENCES public."09_unified_documents"(id)
  ON DELETE CASCADE;

DO $$
BEGIN
  RAISE NOTICE '10_ix_search_index.doc_id → 09_unified_documents.id FK 追加完了';
END $$;
