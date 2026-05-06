-- ============================================================
-- 03_ema_classroom_01_raw: file_id（重複抑止・GAS 同期用）
-- ============================================================
-- IKUYA 用 04 テーブルと同様、PostgREST on_conflict=file_id を使うための列と一意インデックス。

ALTER TABLE public."03_ema_classroom_01_raw"
  ADD COLUMN IF NOT EXISTS file_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_03_ema_classroom_file_id
  ON public."03_ema_classroom_01_raw"(file_id)
  WHERE file_id IS NOT NULL;

COMMENT ON COLUMN public."03_ema_classroom_01_raw".file_id IS
  '重複チェック用キー（Drive ファイル ID または 投稿ID_text）。GAS 同期で使用。';
