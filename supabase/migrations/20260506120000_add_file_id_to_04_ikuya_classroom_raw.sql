-- ============================================================
-- 04_ikuya_classroom_01_raw: file_id（重複抑止・GAS 同期用）
-- ============================================================
-- Classroom 投入は同一 Drive 添付や同一投稿の再実行で重複しうる。
-- PostgREST の on_conflict=file_id を使うため、列と部分一意インデックスを追加する。

ALTER TABLE public."04_ikuya_classroom_01_raw"
  ADD COLUMN IF NOT EXISTS file_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_04_ikuya_classroom_file_id
  ON public."04_ikuya_classroom_01_raw"(file_id)
  WHERE file_id IS NOT NULL;

COMMENT ON COLUMN public."04_ikuya_classroom_01_raw".file_id IS
  '重複チェック用キー（Drive ファイル ID または 投稿ID_text）。GAS 同期で使用。';
