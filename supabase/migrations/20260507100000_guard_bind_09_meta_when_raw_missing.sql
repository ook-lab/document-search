-- 09 INSERT 時の meta 同期: raw キーが欠ける行では触れない（meta.raw_id NOT NULL 違反の防止）
CREATE OR REPLACE FUNCTION public.bind_09_doc_id_to_meta()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.raw_id IS NULL OR NEW.raw_table IS NULL OR btrim(NEW.raw_table) = '' THEN
    RETURN NEW;
  END IF;
  IF NEW.raw_table IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  ) THEN
    INSERT INTO public."09_unified_documents_meta"(raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
    VALUES (NEW.raw_table, NEW.raw_id, NEW.id, NULL, now())
    ON CONFLICT (raw_table, raw_id) DO UPDATE
      SET doc_id = EXCLUDED.doc_id,
          updated_at = now();
  END IF;
  RETURN NEW;
END;
$$;

DO $$
BEGIN
  RAISE NOTICE 'bind_09_doc_id_to_meta: skip when raw_id/raw_table missing';
END $$;
