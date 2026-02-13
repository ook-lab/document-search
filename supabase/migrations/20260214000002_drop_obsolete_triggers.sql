-- ============================================
-- 削除済みカラムを参照する古いトリガーを削除
-- processing_started_at などを参照するトリガーをクリーンアップ
-- ============================================

-- ============================================
-- 1. トリガー調査（実行して確認用）
-- ============================================
-- 以下のクエリで Rawdata_FILE_AND_MAIL のトリガーを確認
/*
SELECT
    trigger_name,
    event_manipulation,
    action_timing,
    action_orientation
FROM information_schema.triggers
WHERE event_object_table = 'Rawdata_FILE_AND_MAIL'
ORDER BY trigger_name;
*/

-- ============================================
-- 2. 疑わしいトリガーを削除
-- ============================================
-- processing_started_at を参照している可能性のあるトリガー
-- （データベースに残っている古い定義）

-- trg_track_processing: processing_started_at を設定していた可能性
DROP TRIGGER IF EXISTS trg_track_processing ON "Rawdata_FILE_AND_MAIL";
DROP FUNCTION IF EXISTS fn_track_processing();

-- trg_enqueue_failed: failed_stage を参照していた可能性
DROP TRIGGER IF EXISTS trg_enqueue_failed ON "Rawdata_FILE_AND_MAIL";
DROP FUNCTION IF EXISTS fn_enqueue_failed();

-- trg_guard_completed: 完了時のガード（不要）
DROP TRIGGER IF EXISTS trg_guard_completed ON "Rawdata_FILE_AND_MAIL";
DROP FUNCTION IF EXISTS fn_guard_completed();

-- trg_guard_skipped: スキップ時のガード（不要）
DROP TRIGGER IF EXISTS trg_guard_skipped ON "Rawdata_FILE_AND_MAIL";
DROP FUNCTION IF EXISTS fn_guard_skipped();

-- ============================================
-- 3. 確認ログ
-- ============================================
DO $$
BEGIN
    RAISE NOTICE '✅ drop_obsolete_triggers.sql 適用完了';
    RAISE NOTICE '  - 削除済みカラムを参照する古いトリガーを削除';
    RAISE NOTICE '  - trg_track_processing, trg_enqueue_failed, trg_guard_completed, trg_guard_skipped';
END $$;
