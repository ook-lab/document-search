-- 20260929000001_clear_spreadsheet_error.sql
-- Google スプレッドシートを PDF 書き出しで読めるようにしたため、「2026年度セクション・係」の
-- 「対応外のファイル形式」の失敗の記録を消して、次のパイプラインの回で読み直させる（ユーザー承認済み 2026-09-29）。
-- ちょうど1件でなければ中止。削除系文は含めない。
DO $$
DECLARE n int;
BEGIN
  UPDATE public."09_unified_documents_meta"
     SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL
   WHERE raw_table = '03_ema_classroom_01_raw'
     AND raw_id::text LIKE 'ad3d9b08%'
     AND ix_pipeline_error LIKE '%application/vnd.google-apps.spreadsheet%';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'スプレッドシートの記録の更新件数が % 件', n; END IF;
END $$;
