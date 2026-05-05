-- ============================================================
-- fast-index: PDF由来MDを raw 行の付属データとして保持する
-- ============================================================

ALTER TABLE public."03_ema_classroom_01_raw"
  ADD COLUMN IF NOT EXISTS pdf_md_content TEXT,
  ADD COLUMN IF NOT EXISTS pdf_md_updated_at TIMESTAMPTZ;

ALTER TABLE public."04_ikuya_classroom_01_raw"
  ADD COLUMN IF NOT EXISTS pdf_md_content TEXT,
  ADD COLUMN IF NOT EXISTS pdf_md_updated_at TIMESTAMPTZ;

ALTER TABLE public."05_ikuya_waseaca_01_raw"
  ADD COLUMN IF NOT EXISTS pdf_md_content TEXT,
  ADD COLUMN IF NOT EXISTS pdf_md_updated_at TIMESTAMPTZ;

ALTER TABLE public."08_file_only_01_raw"
  ADD COLUMN IF NOT EXISTS pdf_md_content TEXT,
  ADD COLUMN IF NOT EXISTS pdf_md_updated_at TIMESTAMPTZ;

COMMENT ON COLUMN public."03_ema_classroom_01_raw".pdf_md_content IS '添付PDFから抽出・確認したMarkdown本文';
COMMENT ON COLUMN public."04_ikuya_classroom_01_raw".pdf_md_content IS '添付PDFから抽出・確認したMarkdown本文';
COMMENT ON COLUMN public."05_ikuya_waseaca_01_raw".pdf_md_content IS '添付PDFから抽出・確認したMarkdown本文';
COMMENT ON COLUMN public."08_file_only_01_raw".pdf_md_content IS '添付PDFから抽出・確認したMarkdown本文';

COMMENT ON COLUMN public."03_ema_classroom_01_raw".pdf_md_updated_at IS 'pdf_md_content の最終更新時刻';
COMMENT ON COLUMN public."04_ikuya_classroom_01_raw".pdf_md_updated_at IS 'pdf_md_content の最終更新時刻';
COMMENT ON COLUMN public."05_ikuya_waseaca_01_raw".pdf_md_updated_at IS 'pdf_md_content の最終更新時刻';
COMMENT ON COLUMN public."08_file_only_01_raw".pdf_md_updated_at IS 'pdf_md_content の最終更新時刻';

-- 旧設計で pipeline_meta.md_content に入ったPDF由来MDを raw 側へ退避する。
UPDATE public."03_ema_classroom_01_raw" raw
SET pdf_md_content = pm.md_content,
    pdf_md_updated_at = COALESCE(pm.updated_at, now())
FROM public.pipeline_meta pm
WHERE pm.raw_table = '03_ema_classroom_01_raw'
  AND pm.raw_id = raw.id
  AND COALESCE(pm.md_content, '') <> ''
  AND COALESCE(raw.pdf_md_content, '') = '';

UPDATE public."04_ikuya_classroom_01_raw" raw
SET pdf_md_content = pm.md_content,
    pdf_md_updated_at = COALESCE(pm.updated_at, now())
FROM public.pipeline_meta pm
WHERE pm.raw_table = '04_ikuya_classroom_01_raw'
  AND pm.raw_id = raw.id
  AND COALESCE(pm.md_content, '') <> ''
  AND COALESCE(raw.pdf_md_content, '') = '';

UPDATE public."05_ikuya_waseaca_01_raw" raw
SET pdf_md_content = pm.md_content,
    pdf_md_updated_at = COALESCE(pm.updated_at, now())
FROM public.pipeline_meta pm
WHERE pm.raw_table = '05_ikuya_waseaca_01_raw'
  AND pm.raw_id = raw.id
  AND COALESCE(pm.md_content, '') <> ''
  AND COALESCE(raw.pdf_md_content, '') = '';

UPDATE public."08_file_only_01_raw" raw
SET pdf_md_content = pm.md_content,
    pdf_md_updated_at = COALESCE(pm.updated_at, now())
FROM public.pipeline_meta pm
WHERE pm.raw_table = '08_file_only_01_raw'
  AND pm.raw_id = raw.id
  AND COALESCE(pm.md_content, '') <> ''
  AND COALESCE(raw.pdf_md_content, '') = '';

DO $$
BEGIN
  RAISE NOTICE 'raw tables: pdf_md_content / pdf_md_updated_at を追加しました';
END $$;
