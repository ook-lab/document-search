-- Rawdata_FILE_AND_MAIL にカレンダーイベント用の開始・終了日時カラムを追加
ALTER TABLE "Rawdata_FILE_AND_MAIL"
    ADD COLUMN IF NOT EXISTS start_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS end_ts   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_rawdata_start_ts
    ON "Rawdata_FILE_AND_MAIL" (start_ts)
    WHERE start_ts IS NOT NULL;
