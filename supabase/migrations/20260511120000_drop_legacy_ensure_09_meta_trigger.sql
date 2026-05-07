-- 09_unified_documents_meta は (raw_table, raw_id) が PK で NOT NULL。
-- 旧 ensure_09_meta_for_search_targets は doc_id のみ INSERT し、raw 再キー化後のスキーマと両立しない。
-- 同一 INSERT 上では bind_09_doc_id_to_meta が raw キー付きで upsert するため、このトリガーは冗長かつ有害。

DROP TRIGGER IF EXISTS trg_ensure_09_meta_for_search_targets_insert
  ON public."09_unified_documents";

DROP FUNCTION IF EXISTS public.ensure_09_meta_for_search_targets();
