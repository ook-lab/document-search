-- 09_unified_documents に存在しない doc_id のチャンクは検索上ノイズかつ整合しないため削除する。
-- （10_ix は 09 への FK が無く、09 削除後に残り得る）

DELETE FROM public."10_ix_search_index" t
WHERE NOT EXISTS (
  SELECT 1 FROM public."09_unified_documents" u WHERE u.id = t.doc_id
);

DO $$
BEGIN
  RAISE NOTICE '10_ix_search_index: orphan doc_id rows removed';
END $$;
