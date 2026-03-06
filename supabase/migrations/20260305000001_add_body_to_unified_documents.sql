-- 09_unified_documents に本文フルテキストカラムを追加
ALTER TABLE public."09_unified_documents"
  ADD COLUMN IF NOT EXISTS body TEXT;

COMMENT ON COLUMN public."09_unified_documents".body IS 'メール本文フルテキスト（Gmail: body_plain）';

DO $$
BEGIN
  RAISE NOTICE '09_unified_documents.body カラム追加完了';
END $$;
