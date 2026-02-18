-- ============================================================
-- 不足カラムを一括追加
--
-- 背景:
--   以下のカラムがコードから参照されているが DB に存在しない。
--   create_status_guard.sql / nack_document RPC / pipeline_manager.py 等が参照。
--
-- 実行: Supabase SQL Editor で一括実行
-- ============================================================

-- ① error_message
--    status_guard トリガーが failed 落とし時に理由を記録するカラム
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS error_message TEXT;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".error_message
  IS 'ステータスガードトリガーが failed に落とした際の理由';

-- ② failed_at
--    nack_document RPC が failed 確定時に記録するカラム
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".failed_at
  IS '最終失敗確定時刻（nack_document RPC が記録）';

-- ③ last_error_reason
--    nack_document RPC がエラー理由を記録するカラム
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS last_error_reason TEXT;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".last_error_reason
  IS 'nack時の最終エラー理由（p_error_message）';

-- ④ last_worker
--    ack/nack RPC が処理ワーカー名を記録するカラム
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS last_worker TEXT;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".last_worker
  IS '最後に処理したワーカー識別子';

-- ⑤ last_attempt_at
--    nack_document RPC が最終試行時刻を記録するカラム
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".last_attempt_at
  IS '最終試行時刻（nack_document RPC が記録）';

-- ⑥ completed_at
--    ack_document RPC が完了時刻を記録するカラム
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".completed_at
  IS '処理完了時刻（ack_document RPC が記録）';

-- ⑦ pdf_creator
--    pipeline_manager.py の Stage A 結果保存で使用
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS pdf_creator TEXT;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".pdf_creator
  IS 'PDFメタデータの Creator フィールド（作成ソフト）';

-- ⑧ pdf_producer
--    pipeline_manager.py の Stage A 結果保存で使用
ALTER TABLE public."Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS pdf_producer TEXT;

COMMENT ON COLUMN public."Rawdata_FILE_AND_MAIL".pdf_producer
  IS 'PDFメタデータの Producer フィールド（変換ソフト）';
