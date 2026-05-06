-- 09_unified_documents_meta の対象に 08_file_only_01_raw を追加する。
-- 既存トリガー/関数は置き換える。

CREATE OR REPLACE FUNCTION public.ensure_09_meta_for_search_targets()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.raw_table IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  ) THEN
    INSERT INTO public."09_unified_documents_meta"(doc_id, ix_vectorized_at, updated_at)
    VALUES (NEW.id, NULL, now())
    ON CONFLICT (doc_id) DO NOTHING;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ensure_09_meta_for_classroom_insert
  ON public."09_unified_documents";
DROP TRIGGER IF EXISTS trg_ensure_09_meta_for_search_targets_insert
  ON public."09_unified_documents";

CREATE TRIGGER trg_ensure_09_meta_for_search_targets_insert
AFTER INSERT ON public."09_unified_documents"
FOR EACH ROW
EXECUTE FUNCTION public.ensure_09_meta_for_search_targets();

-- 既存 09 のうち、03/04/05/08 に不足している meta 行を補完
INSERT INTO public."09_unified_documents_meta" (doc_id, ix_vectorized_at, updated_at)
SELECT ud.id, NULL, now()
FROM public."09_unified_documents" ud
LEFT JOIN public."09_unified_documents_meta" um
  ON um.doc_id = ud.id
WHERE ud.raw_table IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  )
  AND um.doc_id IS NULL;

DO $$
BEGIN
  RAISE NOTICE '09_unified_documents_meta の対象に 08_file_only_01_raw を追加しました';
END $$;

