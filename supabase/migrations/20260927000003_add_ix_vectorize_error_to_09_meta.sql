-- 09_unified_documents_meta にベクトル化失敗情報記録用カラムを追加
ALTER TABLE public."09_unified_documents_meta"
    ADD COLUMN IF NOT EXISTS ix_vectorize_error text,
    ADD COLUMN IF NOT EXISTS ix_vectorize_error_at timestamptz;
