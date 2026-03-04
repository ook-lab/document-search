-- ============================================================
-- calendar_events テーブル削除
-- google-calendar-sync 廃止に伴い中間ステージングテーブルを除去。
-- 役割は calendar-index-sync が直接 02_gcal_01_raw に担う。
-- ============================================================

DROP TABLE IF EXISTS public.calendar_events CASCADE;

DO $$
BEGIN
  RAISE NOTICE 'calendar_events テーブル削除完了';
END $$;
