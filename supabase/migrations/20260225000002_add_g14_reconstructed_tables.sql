-- G-14 新設に伴う新規カラム追加

ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS g14_reconstructed_tables JSONB;

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".g14_reconstructed_tables IS 'G-14: 繰り返しヘッダー検出による表再構成データ';
