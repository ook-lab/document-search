-- =====================================================
-- Supabase カラム追加
-- テーブル: Rawdata_FILE_AND_MAIL
-- Stage A (A3, A5) で取得した PDF メタデータ・判定結果を保存
-- =====================================================

-- PDF Creator（作成元アプリケーション）
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS "pdf_creator" TEXT;

-- PDF Producer（PDF生成エンジン）
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS "pdf_producer" TEXT;

-- Origin App（作成ソフト）A-5 判定
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS "origin_app" TEXT;

-- Layout Profile（レイアウト特性）A-3 判定
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS "layout_profile" TEXT;

-- カラムにコメントを追加
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL"."pdf_creator"
IS 'PDF metadata: Creator field (e.g., Microsoft Word, Google Docs)';

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL"."pdf_producer"
IS 'PDF metadata: Producer field (e.g., Skia/PDF m146 Google Docs Renderer)';

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL"."origin_app"
IS 'Stage A判定: 作成ソフト (WORD, INDESIGN, GOODNOTES, GOOGLE_DOCS, etc.)';

COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL"."layout_profile"
IS 'Stage A判定: レイアウト特性/処理難易度 (FLOW, FIXED, HYBRID)';

-- =====================================================
-- 実行後の確認クエリ
-- =====================================================
-- SELECT column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'Rawdata_FILE_AND_MAIL'
--   AND column_name IN ('pdf_creator', 'pdf_producer', 'origin_app', 'layout_profile')
-- ORDER BY ordinal_position;
