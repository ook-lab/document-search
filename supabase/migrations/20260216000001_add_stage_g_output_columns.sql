-- Add Stage G output columns to Rawdata_FILE_AND_MAIL
-- G-11: 構造化表データ
-- G-12: AI処理済み表データ
-- G-21: 記事データ
-- G-22: AI抽出データ

ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS g11_structured_tables JSONB,
ADD COLUMN IF NOT EXISTS g12_table_analyses JSONB,
ADD COLUMN IF NOT EXISTS g21_articles JSONB,
ADD COLUMN IF NOT EXISTS g22_ai_extracted JSONB;

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".g11_structured_tables IS 'G-11: 構造化された表データ（headers/rows形式）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".g12_table_analyses IS 'G-12: AI処理済み表データ（reshaped形式）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".g21_articles IS 'G-21: 記事データ（地の文）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".g22_ai_extracted IS 'G-22: AI抽出データ（イベント・タスク・注意事項）';
