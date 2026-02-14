-- ============================================
-- latest_correction_id カラムを追加
-- レビュー済み判定と修正履歴機能に必要
-- ============================================

-- ============================================
-- 1. カラム追加
-- ============================================
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS latest_correction_id BIGINT REFERENCES "99_lg_correction_history"(id);

-- ============================================
-- 2. インデックス作成（レビューステータスのフィルタリング高速化）
-- ============================================
CREATE INDEX IF NOT EXISTS idx_rawdata_latest_correction_id
ON "Rawdata_FILE_AND_MAIL"(latest_correction_id);

-- ============================================
-- 3. コメント
-- ============================================
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".latest_correction_id IS '最新の修正履歴ID（99_lg_correction_history への参照）。NULL=未レビュー、NOT NULL=レビュー済み';

-- ============================================
-- 4. 確認ログ
-- ============================================
DO $$
BEGIN
    RAISE NOTICE '✅ add_latest_correction_id.sql 適用完了';
    RAISE NOTICE '  - latest_correction_id カラムを追加';
    RAISE NOTICE '  - レビューステータスの判定に使用';
END $$;
