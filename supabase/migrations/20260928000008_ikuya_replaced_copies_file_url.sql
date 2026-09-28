-- 20260928000008_ikuya_replaced_copies_file_url.sql
-- 育哉の10件: 差し替えスクリプトの不具合で Supabase の file_url が消えた旧コピーを指したままだったため、
-- 差し替え後の新しいコピー（ookubo.y@gmail.com 所有、ファイル名末尾に [元ファイル番号] を含む、ゴミ箱外）の URL に書き換える。
-- 同じ元ファイルのコピーが2つある場合は作成時刻が新しい方。各行は旧 URL 一致を条件に1件だけ更新し、1件でなければ中止。
-- 失敗の記録(ix_pipeline_error)も消して次のバッチで読み直させる（ユーザー承認済み 2026-09-28）。削除系文は含めない。
DO $$
DECLARE n int;
BEGIN
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1sbkySkssAsj0_pgjH4YvA9imQPvCH00I/view?usp=drivesdk' WHERE id = 'cb094cd6-6cd9-4ecf-ba7c-ac047100e35d' AND file_url = 'https://drive.google.com/file/d/1c5JY0aTvvdR2hY1Zbxz2I2mIfvGbfDjC/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw cb094cd6-6cd9-4ecf-ba7c-ac047100e35d の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'cb094cd6-6cd9-4ecf-ba7c-ac047100e35d';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta cb094cd6-6cd9-4ecf-ba7c-ac047100e35d の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1xHMpHBbxw_Kj2GoaJBQksg5OOlH6uNZp/view?usp=drivesdk' WHERE id = '6e036caa-6df8-468e-8042-4263120a7df1' AND file_url = 'https://drive.google.com/file/d/1tzcRAOkdZJqzjsekLEILBS_sRa6Bx_rH/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw 6e036caa-6df8-468e-8042-4263120a7df1 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = '6e036caa-6df8-468e-8042-4263120a7df1';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta 6e036caa-6df8-468e-8042-4263120a7df1 の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1sfTIo_eg6K_0EtvVoTbsF0Z6VTZ5qTmQ/view?usp=drivesdk' WHERE id = 'b66659fb-c10c-449d-9b59-7557995c443d' AND file_url = 'https://drive.google.com/file/d/16SSGS5zGK6j3ZMNF4_fP8r4waoo9MUmU/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw b66659fb-c10c-449d-9b59-7557995c443d の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'b66659fb-c10c-449d-9b59-7557995c443d';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta b66659fb-c10c-449d-9b59-7557995c443d の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1MvgQU_s57cJdbVuYJMU7zPZuMf0jjUms/view?usp=drivesdk' WHERE id = '483a2a71-5f55-49ef-aac1-dde83dd51ecd' AND file_url = 'https://drive.google.com/file/d/1PCJalU-xXZ9M2zC0WBMkzlhFcCGBUpIe/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw 483a2a71-5f55-49ef-aac1-dde83dd51ecd の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = '483a2a71-5f55-49ef-aac1-dde83dd51ecd';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta 483a2a71-5f55-49ef-aac1-dde83dd51ecd の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/18D4szxz1n1k46iymKr8v_tjynKULdMZm/view?usp=drivesdk' WHERE id = '18fad812-1f94-4cba-8cfb-f16f2c22d81d' AND file_url = 'https://drive.google.com/file/d/1N7j5aNpOH5zufRwYocEM7z4RkPpOCsLC/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw 18fad812-1f94-4cba-8cfb-f16f2c22d81d の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = '18fad812-1f94-4cba-8cfb-f16f2c22d81d';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta 18fad812-1f94-4cba-8cfb-f16f2c22d81d の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1cn6HytCd4Rx2x-qJq3IDTssGD3DxiLH9/view?usp=drivesdk' WHERE id = 'e1ddde23-246b-40bd-a831-f23aa7f9d236' AND file_url = 'https://drive.google.com/file/d/1PnYq9wyJ9PwPzmry60hYKH3xLDRC_iBP/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw e1ddde23-246b-40bd-a831-f23aa7f9d236 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'e1ddde23-246b-40bd-a831-f23aa7f9d236';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta e1ddde23-246b-40bd-a831-f23aa7f9d236 の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1iegPz30hjmvjxIX-A808rPV1BKRRpy8L/view?usp=drivesdk' WHERE id = 'c24ad08b-cc74-4228-adf4-cef5a1af5c66' AND file_url = 'https://drive.google.com/file/d/1hKWDR_HgSaPuC3MkHRB4HnfIMoV3AvIW/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw c24ad08b-cc74-4228-adf4-cef5a1af5c66 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'c24ad08b-cc74-4228-adf4-cef5a1af5c66';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta c24ad08b-cc74-4228-adf4-cef5a1af5c66 の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1QNwsVAK3W_5XaDv1dBTx1e68HANHbyxB/view?usp=drivesdk' WHERE id = 'd9abd42c-0080-494f-8fb6-82713f09b5ed' AND file_url = 'https://drive.google.com/file/d/1JJnVoUMiR95VSGs644KC0mR5D6X0P4iT/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw d9abd42c-0080-494f-8fb6-82713f09b5ed の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = 'd9abd42c-0080-494f-8fb6-82713f09b5ed';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta d9abd42c-0080-494f-8fb6-82713f09b5ed の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/1AqMvWdanN2uk8VHE7XdKDz8k_XluPzQj/view?usp=drivesdk' WHERE id = '1c5bfa8b-8180-408d-a97d-20e55666aa56' AND file_url = 'https://drive.google.com/file/d/1rlSkW0PFpZQGkUgKcI7dZ5080jkPqS95/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw 1c5bfa8b-8180-408d-a97d-20e55666aa56 の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = '1c5bfa8b-8180-408d-a97d-20e55666aa56';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta 1c5bfa8b-8180-408d-a97d-20e55666aa56 の更新件数が % 件', n; END IF;
  UPDATE public."04_ikuya_classroom_01_raw" SET file_url = 'https://drive.google.com/file/d/19eRp4Z6adMOL6Oce50kXYPIceIoQe5Ym/view?usp=drivesdk' WHERE id = '1200fafd-efbd-481b-9e11-55c73712833a' AND file_url = 'https://drive.google.com/file/d/1HXba5RWFIIoxzjC03dp_CmDZkuYGpbU0/view?usp=drivesdk';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION '04 raw 1200fafd-efbd-481b-9e11-55c73712833a の更新件数が % 件', n; END IF;
  UPDATE public."09_unified_documents_meta" SET ix_pipeline_error = NULL, ix_pipeline_error_at = NULL WHERE raw_table = '04_ikuya_classroom_01_raw' AND raw_id = '1200fafd-efbd-481b-9e11-55c73712833a';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 1 THEN RAISE EXCEPTION 'meta 1200fafd-efbd-481b-9e11-55c73712833a の更新件数が % 件', n; END IF;
END $$;
