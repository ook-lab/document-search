-- 20260928000006_reindex_classroom_post_body_chunks.sql
-- クラスルーム文書を「投稿の本文(post_body)」と「添付ファイルの中身」を別々の断片で登録し直すため、
-- ベクトル化済みの印(ix_vectorized_at)をクラスルーム(03/04)の行だけ未処理に戻す。
-- 旧断片(10_ix_search_index)は消さない。rag-prepare の再登録時に doc_id 単位で新断片へ置き換わる。
-- 他ソース（Gmail・カレンダー・早稲アカ・ファイル）は対象外。削除系文は含めない。

UPDATE public."09_unified_documents_meta"
   SET ix_vectorized_at = NULL
 WHERE raw_table IN ('03_ema_classroom_01_raw', '04_ikuya_classroom_01_raw')
   AND ix_vectorized_at IS NOT NULL;
