-- ============================================================
-- 05_ikuya_waseaca_01_raw: 複数添付PDF対応のための pdf_slot 列追加
-- ============================================================
-- 1つのお知らせに複数PDFが添付されるケースにおいて、取込済み判定を
-- (post_id, pdf_slot) 単位で行えるよう整数列 pdf_slot を追加する。
-- 既存データは NULL のまま保持し、推測による値補完は行わない（フォールバック禁止）。

ALTER TABLE public."05_ikuya_waseaca_01_raw"
  ADD COLUMN IF NOT EXISTS pdf_slot INTEGER;

-- 検索・取込済み判定の高速化用インデックス
CREATE INDEX IF NOT EXISTS idx_05_ikuya_waseaca_post_id_pdf_slot
  ON public."05_ikuya_waseaca_01_raw"(post_id, pdf_slot);

COMMENT ON COLUMN public."05_ikuya_waseaca_01_raw".pdf_slot IS
  '同一お知らせ内の添付PDF番号（0始まりの整数）。テキストのみまたは既存行はNULL。';
