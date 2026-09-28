-- 20260928000009_clear_fixed_errors.sql
-- 原因を直してデプロイ済みのエラーについて、失敗の記録を消して再処理させる（ユーザー承認済み 2026-09-28）。
-- ベクトル化失敗: 表YAMLの折り返し崩れ(作り直し済み・読み戻し確認済み)、AI構造解析の応答形式(response_schema で固定)。
-- パイプライン失敗: 画像が大きすぎ(Files API 経由に対応)、ブロック空(写真の回転情報を反映)、Word(LibreOffice で PDF 化)。
-- 各行ちょうど1件の更新でなければ中止。削除系文は含めない。
DO $$
DECLARE n int;
BEGIN
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = '6e8634e6-bafe-449a-a3bc-bbc24613474d';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '6e8634e6-bafe-449a-a3bc-bbc24613474d の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = '0a1b9efe-3189-4a22-a29d-10880ce80d93';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '0a1b9efe-3189-4a22-a29d-10880ce80d93 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = '21c76d11-636e-4c5b-9190-6fb993e4971d';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '21c76d11-636e-4c5b-9190-6fb993e4971d の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = '6894bf90-a03d-4c50-8046-6cf1ef003805';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '6894bf90-a03d-4c50-8046-6cf1ef003805 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'b58cc714-7afd-47ab-9aec-7866c2d11911';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'b58cc714-7afd-47ab-9aec-7866c2d11911 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'c6ce0933-7c6e-404a-b465-68bf5a89277f';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'c6ce0933-7c6e-404a-b465-68bf5a89277f の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = '3561dd65-f77a-47a4-9aff-fcc7befcbb9f';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '3561dd65-f77a-47a4-9aff-fcc7befcbb9f の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'd04fc9e1-a600-48d2-b4ce-1510249ab049';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'd04fc9e1-a600-48d2-b4ce-1510249ab049 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = '5c9fd5ef-cbb3-4b3e-868a-04cd3a8cb86b';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '5c9fd5ef-cbb3-4b3e-868a-04cd3a8cb86b の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '05_ikuya_waseaca_01_raw' AND raw_id = '89a23eb8-b7f3-4caf-b7f7-315611a5c2e1';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '89a23eb8-b7f3-4caf-b7f7-315611a5c2e1 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_vectorize_error = NULL, ix_vectorize_error_at = NULL WHERE raw_table = '05_ikuya_waseaca_01_raw' AND raw_id = 'a8a79950-7640-464f-8f95-dd28f5e514e8';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'a8a79950-7640-464f-8f95-dd28f5e514e8 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = 'a42ffae6-94bd-4977-9537-39a06da17341';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'a42ffae6-94bd-4977-9537-39a06da17341 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = '0c0c5fe0-9930-49f4-b661-822896533d4b';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '0c0c5fe0-9930-49f4-b661-822896533d4b の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = 'bc14cc82-a1a6-4c05-b2b0-ed92e31f8068';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'bc14cc82-a1a6-4c05-b2b0-ed92e31f8068 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = 'bff85d33-29a0-4a20-9488-1e1394ed90f0';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'bff85d33-29a0-4a20-9488-1e1394ed90f0 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = 'b6cf445e-d669-4047-9a30-a9c355af65ea';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'b6cf445e-d669-4047-9a30-a9c355af65ea の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = 'ddb47096-397e-4271-be38-fd5a8b5fe9f7';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'ddb47096-397e-4271-be38-fd5a8b5fe9f7 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '03_ema_classroom_01_raw' AND raw_id = '8481689d-3fc1-418a-888a-655aa902a743';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '8481689d-3fc1-418a-888a-655aa902a743 の更新件数が % 件', n; END IF;
END $$;
