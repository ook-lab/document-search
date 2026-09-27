-- ============================================================
-- 09_unified_documents.meta.file_name を raw 表の file_name 実値で補完
-- ============================================================
-- 検索画面の添付ラベル「投稿題名 / ファイル名」表示用。
-- rag-prepare が 09 行を作る際 meta に file_name を入れていなかったため、既存行に raw の実値を写す。
-- raw の file_name が NULL の行は対象外（推測で埋めない。画面では「(ファイル名なし)」と表示される）。

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    '03_ema_classroom_01_raw',
    '04_ikuya_classroom_01_raw',
    '05_ikuya_waseaca_01_raw',
    '08_file_only_01_raw'
  ] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = t AND column_name = 'file_name'
    ) THEN
      RAISE EXCEPTION 'raw table % has no file_name column', t;
    END IF;

    EXECUTE format(
      'UPDATE public."09_unified_documents" ud
          SET meta = COALESCE(ud.meta, ''{}''::jsonb) || jsonb_build_object(''file_name'', r.file_name)
         FROM public.%I r
        WHERE ud.raw_table = %L
          AND ud.raw_id::text = r.id::text
          AND r.file_name IS NOT NULL',
      t, t
    );
  END LOOP;
END $$;
