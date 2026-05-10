-- ============================================================
-- Classroom GAS（anon）→ raw INSERT 時の 09_meta トリガー権限
-- ============================================================
-- 事象: anon が 03/04/05/08 raw に INSERT すると AFTER トリガーが
--   09_unified_documents_meta へ INSERT する。トリガー関数が INVOKER のままだと
--   実行主体は anon となり、09 への GRANT が無ければ
--   「permission denied for table 09_unified_documents_meta」となる。
-- 方針: anon に 09 を直接 GRANT しない（PostgREST 経由の直接書き込みを防ぐ）。
--   当該トリガー関数のみ SECURITY DEFINER + search_path 固定とする。
-- 運用: GAS の SUPABASE_KEY は anon（publishable）のみ。service_role は GAS に置かない。
--   （Supabase がクライアント扱いで secret キーを拒否する事例あり）

CREATE OR REPLACE FUNCTION public.ensure_09_meta_on_raw_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF TG_TABLE_NAME IN (
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  ) THEN
    INSERT INTO public."09_unified_documents_meta"(raw_table, raw_id, doc_id, ix_vectorized_at, updated_at)
    VALUES (TG_TABLE_NAME, NEW.id, NULL, NULL, now())
    ON CONFLICT (raw_table, raw_id) DO UPDATE
      SET updated_at = EXCLUDED.updated_at;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.bind_09_doc_id_to_meta()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
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

ALTER FUNCTION public.ensure_09_meta_on_raw_insert() OWNER TO postgres;
ALTER FUNCTION public.bind_09_doc_id_to_meta() OWNER TO postgres;

REVOKE ALL ON FUNCTION public.ensure_09_meta_on_raw_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.bind_09_doc_id_to_meta() FROM PUBLIC;

DO $$
BEGIN
  RAISE NOTICE 'ensure_09_meta_on_raw_insert / bind_09_doc_id_to_meta を SECURITY DEFINER に更新（GAS anon 対応）';
END $$;
