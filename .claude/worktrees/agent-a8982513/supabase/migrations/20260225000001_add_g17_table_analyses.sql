-- G-12 → G-17 リネームに伴うカラム変更
-- 旧カラム削除 + 新カラム追加

ALTER TABLE "Rawdata_FILE_AND_MAIL"
DROP COLUMN IF EXISTS g12_table_analyses,
ADD COLUMN IF NOT EXISTS g17_table_analyses JSONB;

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".g17_table_analyses IS 'G-17: AI処理済み表データ（sections形式）';
