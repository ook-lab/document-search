-- ============================================================
-- raw テーブル削除時に 09_unified_documents と pipeline_meta を連動削除
-- 全 raw テーブル（01〜08）共通のトリガー関数
-- ============================================================

CREATE OR REPLACE FUNCTION public.fn_cascade_delete_raw()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM public."09_unified_documents"
  WHERE raw_id = OLD.id AND raw_table = TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;

  -- スキーマなしのテーブル名でも一致させる（raw_table は 'public.' なしで保存されている場合）
  DELETE FROM public."09_unified_documents"
  WHERE raw_id = OLD.id AND raw_table = TG_TABLE_NAME;

  DELETE FROM public.pipeline_meta
  WHERE raw_id = OLD.id AND raw_table = TG_TABLE_NAME;

  RETURN OLD;
END;
$$;

-- 01_gmail_01_raw
DROP TRIGGER IF EXISTS trg_cascade_delete_01_gmail ON public."01_gmail_01_raw";
CREATE TRIGGER trg_cascade_delete_01_gmail
  AFTER DELETE ON public."01_gmail_01_raw"
  FOR EACH ROW EXECUTE FUNCTION public.fn_cascade_delete_raw();

-- 02_gcal_01_raw
DROP TRIGGER IF EXISTS trg_cascade_delete_02_gcal ON public."02_gcal_01_raw";
CREATE TRIGGER trg_cascade_delete_02_gcal
  AFTER DELETE ON public."02_gcal_01_raw"
  FOR EACH ROW EXECUTE FUNCTION public.fn_cascade_delete_raw();

-- 03_ema_classroom_01_raw
DROP TRIGGER IF EXISTS trg_cascade_delete_03_ema ON public."03_ema_classroom_01_raw";
CREATE TRIGGER trg_cascade_delete_03_ema
  AFTER DELETE ON public."03_ema_classroom_01_raw"
  FOR EACH ROW EXECUTE FUNCTION public.fn_cascade_delete_raw();

-- 04_ikuya_classroom_01_raw
DROP TRIGGER IF EXISTS trg_cascade_delete_04_ikuya ON public."04_ikuya_classroom_01_raw";
CREATE TRIGGER trg_cascade_delete_04_ikuya
  AFTER DELETE ON public."04_ikuya_classroom_01_raw"
  FOR EACH ROW EXECUTE FUNCTION public.fn_cascade_delete_raw();

-- 05_ikuya_waseaca_01_raw
DROP TRIGGER IF EXISTS trg_cascade_delete_05_waseaca ON public."05_ikuya_waseaca_01_raw";
CREATE TRIGGER trg_cascade_delete_05_waseaca
  AFTER DELETE ON public."05_ikuya_waseaca_01_raw"
  FOR EACH ROW EXECUTE FUNCTION public.fn_cascade_delete_raw();

-- 08_file_only_01_raw
DROP TRIGGER IF EXISTS trg_cascade_delete_08_file ON public."08_file_only_01_raw";
CREATE TRIGGER trg_cascade_delete_08_file
  AFTER DELETE ON public."08_file_only_01_raw"
  FOR EACH ROW EXECUTE FUNCTION public.fn_cascade_delete_raw();

DO $$
BEGIN
  RAISE NOTICE 'raw テーブル削除カスケードトリガー設定完了';
END $$;
