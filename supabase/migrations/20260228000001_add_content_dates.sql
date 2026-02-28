-- ============================================================
-- content_dates DATE[] カラム追加
--
-- 目的:
--   F3 Smart Date Normalizer が文書全体から抽出・正規化した
--   全日付を格納する。display_sent_at（送信日）ではなく、
--   文書コンテンツ内に記載された日付（イベント日、予定日など）。
--
--   例: 2月配信の3月予定表 → content_dates = [2026-03-05, 2026-03-12, ...]
--                              display_sent_at = 2026-02-10
--
-- 利用箇所:
--   - unified_search_v2: content_dates を返す列として追加
--   - client.py _check_date_match: content_dates でスコアブースト
-- ============================================================

ALTER TABLE "Rawdata_FILE_AND_MAIL"
    ADD COLUMN IF NOT EXISTS content_dates DATE[];

-- GINインデックス（配列の高速検索用）
CREATE INDEX IF NOT EXISTS idx_rawdata_content_dates
    ON "Rawdata_FILE_AND_MAIL" USING GIN (content_dates);

DO $$
BEGIN
    RAISE NOTICE '✅ 20260228000001_add_content_dates.sql 適用完了';
    RAISE NOTICE '  - content_dates DATE[] カラム追加';
    RAISE NOTICE '  - GINインデックス作成';
END $$;
