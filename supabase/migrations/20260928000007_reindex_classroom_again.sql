-- 20260928000007_reindex_classroom_again.sql
-- 投稿本文の断片化に AI 構造解析を戻した版で、クラスルーム(03/04)を全件登録し直すため、
-- ベクトル化済みの印と前回のベクトル化失敗の記録(一時的な Gemini 503 等)を未処理に戻す。
-- 旧断片は消さない（再登録時に doc_id 単位で置き換わる）。他ソースは対象外。削除系文は含めない。

UPDATE public."09_unified_documents_meta"
   SET ix_vectorized_at = NULL,
       ix_vectorize_error = NULL,
       ix_vectorize_error_at = NULL
 WHERE raw_table IN ('03_ema_classroom_01_raw', '04_ikuya_classroom_01_raw');
