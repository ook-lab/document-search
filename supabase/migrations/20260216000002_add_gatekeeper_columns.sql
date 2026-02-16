-- Add Gatekeeper (A-5) decision columns to Rawdata_FILE_AND_MAIL

ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS gate_decision TEXT,
ADD COLUMN IF NOT EXISTS gate_block_code TEXT,
ADD COLUMN IF NOT EXISTS gate_block_reason TEXT,
ADD COLUMN IF NOT EXISTS origin_app TEXT,
ADD COLUMN IF NOT EXISTS origin_confidence TEXT,
ADD COLUMN IF NOT EXISTS layout_profile TEXT,
ADD COLUMN IF NOT EXISTS gate_policy_version TEXT;

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".gate_decision IS 'ゲートキーパー判定（PASS/BLOCK）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".gate_block_code IS 'ブロックコード（NOT_ALLOWLISTED/LOW_CONFIDENCE等）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".gate_block_reason IS 'ブロック理由（詳細テキスト）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".origin_app IS '作成ソフト（SCAN/WORD/GOOGLE_DOCS/EXCEL/POWERPOINT/GOODNOTES等）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".origin_confidence IS '信頼度（HIGH/MEDIUM/LOW）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".layout_profile IS 'レイアウト特性（FLOW/FIXED）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".gate_policy_version IS 'ゲートキーパーポリシーバージョン（A5.v1等）';
