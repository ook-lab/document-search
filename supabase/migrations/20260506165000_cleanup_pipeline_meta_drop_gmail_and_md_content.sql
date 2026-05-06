-- pipeline_meta はパイプライン専用（中間 JSON とステータスのみ）。
-- Gmail は別アプリ経路のため pipeline_meta から排除する。

-- 1) Gmail 行を削除（既存データ整理）
DELETE FROM public.pipeline_meta
WHERE raw_table = '01_gmail_01_raw';

-- 2) 今後 Gmail が入らないよう制約を追加
ALTER TABLE public.pipeline_meta
  DROP CONSTRAINT IF EXISTS pipeline_meta_raw_table_not_gmail;
ALTER TABLE public.pipeline_meta
  ADD CONSTRAINT pipeline_meta_raw_table_not_gmail
  CHECK (raw_table IS NULL OR raw_table <> '01_gmail_01_raw');

-- 3) 使っていない列を削除（テキストは raw/09/10 に置く）
ALTER TABLE public.pipeline_meta
  DROP COLUMN IF EXISTS md_content;

DO $$
BEGIN
  RAISE NOTICE 'pipeline_meta: Gmail行削除、not-gmail制約追加、md_content削除 完了';
END $$;

