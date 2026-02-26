-- ============================================================
-- file_id カラム新設（投稿ファイル重複チェック専用）
-- ============================================================
--
-- 【背景】
-- 重複チェックを file_url の regex 抽出に依存していた。
-- Google Drive file ID を直接格納する file_id カラムを新設し、
-- DB レベルで一意制約を持たせる。
-- 既存レコードは NULL のまま（新規投稿分からのみ設定）。
--
-- 【前提】
-- 20260226000002_add_file_url.sql 適用済み
--   → file_url カラムが存在する
--
-- ============================================================
-- 1. カラム追加（既存データは NULL のまま）
-- ============================================================

ALTER TABLE "Rawdata_FILE_AND_MAIL" ADD COLUMN IF NOT EXISTS file_id TEXT;

-- ============================================================
-- 2. UNIQUE INDEX（NULL は除外：テキストのみレコードは NULL のまま）
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_rawdata_file_id
ON "Rawdata_FILE_AND_MAIL"(file_id)
WHERE file_id IS NOT NULL;

-- ============================================================
-- 完了ログ
-- ============================================================

DO $$
BEGIN
    RAISE NOTICE '✅ 20260226000003_add_file_id.sql 適用完了';
    RAISE NOTICE '  - Rawdata_FILE_AND_MAIL: file_id カラム追加（既存データは NULL）';
    RAISE NOTICE '  - UNIQUE INDEX (file_id) WHERE file_id IS NOT NULL';
END $$;
