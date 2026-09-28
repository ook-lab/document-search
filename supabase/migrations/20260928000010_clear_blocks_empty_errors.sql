-- 20260928000010_clear_blocks_empty_errors.sql
-- 文字の無い写真・白紙ページが「blocks が空」で失敗していた5件。写真・図の内容を説明するブロックを追加したので、
-- 失敗の記録を消して次のバッチで読み直させる（ユーザー承認済みの再読み込み 2026-09-28）。各1件でなければ中止。削除系文なし。
DO $$
DECLARE n int; r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['0c0c5fe0','bc14cc82','b6cf445e','ddb47096','bff85d33'] LOOP
    UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL
     WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id::text LIKE r || '%' AND ix_pipeline_error LIKE '%blocks%';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 1 THEN RAISE EXCEPTION '% の更新件数が % 件', r, n; END IF;
  END LOOP;
END $$;
