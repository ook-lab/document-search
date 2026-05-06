-- 09_unified_documents の全行に対して meta 行を作る（ステータス専用）。
-- ix_vectorized_at は既存値を優先し、未処理は NULL のまま。

INSERT INTO public."09_unified_documents_meta" (doc_id, ix_vectorized_at, updated_at)
SELECT ud.id, NULL, now()
FROM public."09_unified_documents" ud
LEFT JOIN public."09_unified_documents_meta" um
  ON um.doc_id = ud.id
WHERE um.doc_id IS NULL;

DO $$
BEGIN
  RAISE NOTICE '09_unified_documents_meta の不足行を 09 全件分バックフィルしました';
END $$;

