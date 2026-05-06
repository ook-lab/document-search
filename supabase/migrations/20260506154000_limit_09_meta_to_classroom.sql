-- 09_unified_documents_meta は 03–05 classroom 系と 08 file-only を対象。
-- 01 は別アプリ経路、02 は自動生成で準備ステップ無しのため meta を持たない。

-- 1) 対象外 raw_table の meta 行を削除
DELETE FROM public."09_unified_documents_meta" um
USING public."09_unified_documents" ud
WHERE um.doc_id = ud.id
  AND ud.raw_table NOT IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  );

-- 2) 03–05 の 09 行に不足している meta 行を補完
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

-- 3) 09 へ新規行が入ったとき、03–05 と 08 のみ meta 行を自動作成
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

DROP TRIGGER IF EXISTS trg_ensure_09_meta_for_search_targets_insert
  ON public."09_unified_documents";
CREATE TRIGGER trg_ensure_09_meta_for_search_targets_insert
AFTER INSERT ON public."09_unified_documents"
FOR EACH ROW
EXECUTE FUNCTION public.ensure_09_meta_for_search_targets();

DO $$
BEGIN
  RAISE NOTICE '09_unified_documents_meta を 03–05 と 08 対象に制限し、自動作成トリガーを設定しました';
END $$;

