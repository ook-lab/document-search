-- text_embedded / text_embedded_at は廃止。
-- 完了（K まで・または fast-index ベクトル化まで）は processing_status = completed のみで表す。

DROP INDEX IF EXISTS public.idx_pm_text_embedded_false;

ALTER TABLE public.pipeline_meta
  DROP COLUMN IF EXISTS text_embedded_at,
  DROP COLUMN IF EXISTS text_embedded;

DO $$
BEGIN
  RAISE NOTICE 'pipeline_meta: text_embedded 列を削除しました';
END $$;
