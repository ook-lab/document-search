-- ============================================================
-- 孤立レコードのクリーンアップ
-- 01_gmail_01_raw が削除済みなのに残っている
-- 09_unified_documents と pipeline_meta のレコードを削除
-- ============================================================

-- 削除前に件数確認
DO $$
DECLARE
  ud_count  INT;
  pm_count  INT;
BEGIN
  SELECT COUNT(*) INTO ud_count
  FROM public."09_unified_documents" ud
  WHERE ud.raw_table = '01_gmail_01_raw'
    AND NOT EXISTS (
      SELECT 1 FROM public."01_gmail_01_raw" r WHERE r.id = ud.raw_id
    );

  SELECT COUNT(*) INTO pm_count
  FROM public.pipeline_meta pm
  WHERE pm.raw_table = '01_gmail_01_raw'
    AND NOT EXISTS (
      SELECT 1 FROM public."01_gmail_01_raw" r WHERE r.id = pm.raw_id
    );

  RAISE NOTICE '孤立レコード: 09_unified_documents=% 件 / pipeline_meta=% 件', ud_count, pm_count;
END $$;

-- 09_unified_documents の孤立レコードを削除
DELETE FROM public."09_unified_documents"
WHERE raw_table = '01_gmail_01_raw'
  AND NOT EXISTS (
    SELECT 1 FROM public."01_gmail_01_raw" r WHERE r.id = raw_id
  );

-- pipeline_meta の孤立レコードを削除
DELETE FROM public.pipeline_meta
WHERE raw_table = '01_gmail_01_raw'
  AND NOT EXISTS (
    SELECT 1 FROM public."01_gmail_01_raw" r WHERE r.id = raw_id
  );

DO $$
BEGIN
  RAISE NOTICE 'クリーンアップ完了';
END $$;
