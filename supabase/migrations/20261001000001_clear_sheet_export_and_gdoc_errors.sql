-- 20261001000001_clear_sheet_export_and_gdoc_errors.sql
-- スプレッドシート書き出しの不正引数（supportsAllDrives）を直し、Googleドキュメントを読取対象にしたため、
-- 該当する失敗の記録を消して次のパイプラインの回で読み直させる（ユーザー指示「直すところは直してくれ」2026-10-01）。
-- 件数が想定（スプレッドシート1件、Googleドキュメント6件）と違えば中止。削除系文は含めない。
DO $$
DECLARE n int;
BEGIN
  UPDATE public."09_unified_documents_meta"
     SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL
   WHERE ix_pipeline_error LIKE '%PDFエクスポートに失敗しました%unexpected keyword argument supportsAllDrives%';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'スプレッドシートの記録の更新件数が % 件', n; END IF;

  UPDATE public."09_unified_documents_meta"
     SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL
   WHERE ix_pipeline_error LIKE '対応外のファイル形式です%mime=application/vnd.google-apps.document)%';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 6 THEN RAISE EXCEPTION 'Googleドキュメントの記録の更新件数が % 件', n; END IF;
END $$;
