-- 検索対象にしない（PDF コンテンツをスキップ）フラグ。
-- ix_skip_pdf=TRUE の行は text_only 扱いとして rag-prepare が PDF 抽出をスキップする。

ALTER TABLE public."09_unified_documents_meta"
  ADD COLUMN IF NOT EXISTS ix_skip_pdf BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public."09_unified_documents_meta".ix_skip_pdf IS
  'rag-prepare UI で「検索対象にしない」を押した行。PDF コンテンツを無視して text_only 扱いにする。';
