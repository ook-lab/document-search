-- ============================================
-- パイプライン構造変更に伴うスキーマ更新
-- 2026-01-27
--
-- 変更内容:
-- - Stage E: 5カラム → 1カラムに統合
-- - Stage F: アンカー配列カラム追加
-- - Stage G: 結果カラム追加
-- - Stage H: H1/H2分割に対応
-- ============================================

-- 0. 依存ビューを一時削除（SELECT * を使用しているため）
DROP VIEW IF EXISTS public.v_ops_summary_24h CASCADE;
DROP VIEW IF EXISTS public.v_failed_reasons_7d CASCADE;

-- 1. Stage E: 新カラム追加（統合版）
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS stage_e_text TEXT;

-- 既存データを移行（e4_textの値を使用）
UPDATE "Rawdata_FILE_AND_MAIL"
SET stage_e_text = stage_e4_text
WHERE stage_e_text IS NULL AND stage_e4_text IS NOT NULL;

-- 旧カラムを削除
ALTER TABLE "Rawdata_FILE_AND_MAIL"
DROP COLUMN IF EXISTS stage_e1_text,
DROP COLUMN IF EXISTS stage_e2_text,
DROP COLUMN IF EXISTS stage_e3_text,
DROP COLUMN IF EXISTS stage_e4_text,
DROP COLUMN IF EXISTS stage_e5_text;

-- 2. Stage F: アンカー配列カラム追加
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS stage_f_anchors JSONB;

-- 3. Stage G: 結果カラム追加
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS stage_g_result JSONB;

-- 4. Stage H: H1専用カラム追加
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS stage_h1_tables JSONB;

-- 5. stage_i_structured を stage_h_result にリネーム
-- 注意: カラムが存在する場合のみリネーム
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'Rawdata_FILE_AND_MAIL'
        AND column_name = 'stage_i_structured'
    ) THEN
        ALTER TABLE "Rawdata_FILE_AND_MAIL"
        RENAME COLUMN stage_i_structured TO stage_h_result;
    END IF;
END $$;

-- stage_h_result が存在しない場合は作成
ALTER TABLE "Rawdata_FILE_AND_MAIL"
ADD COLUMN IF NOT EXISTS stage_h_result TEXT;

-- 6. コメント追加（ドキュメント化）
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_e_text IS 'Stage E: 物理抽出テキスト（E-1〜E-3統合）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_f_text_ocr IS 'Stage F: Path A テキスト抽出結果';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_f_layout_ocr IS 'Stage F: レイアウト情報（sections, tables）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_f_visual_elements IS 'Stage F: 視覚要素（diagrams, charts）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_f_anchors IS 'Stage F: アンカーベースのパケット配列（H1/H2ルーティング用）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_g_result IS 'Stage G: 統合精錬結果（source_inventory, table_inventory）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_h_normalized IS 'Stage H2: 入力テキスト（軽量化済み）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_h1_tables IS 'Stage H1: 処理済み表データ';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_h_result IS 'Stage H2: 構造化結果（旧stage_i_structured）';
COMMENT ON COLUMN "Rawdata_FILE_AND_MAIL".stage_j_chunks_json IS 'Stage J: チャンク化結果';

-- 7. ビューを再作成（SELECT * を避けて明示的カラム指定）

-- 7-1. 運用サマリVIEW（24時間）
CREATE OR REPLACE VIEW public.v_ops_summary_24h AS
WITH r AS (
  SELECT processing_status
  FROM public."Rawdata_FILE_AND_MAIL"
  WHERE created_at >= now() - interval '24 hours'
)
SELECT
  now() AS as_of,
  count(*) AS rawdata_24h_total,
  count(*) FILTER (WHERE processing_status = 'pending')    AS pending,
  count(*) FILTER (WHERE processing_status = 'processing') AS processing,
  count(*) FILTER (WHERE processing_status = 'completed')  AS completed,
  count(*) FILTER (WHERE processing_status = 'failed')     AS failed,
  count(*) FILTER (WHERE processing_status = 'skipped')    AS skipped,
  (SELECT count(*) FROM public.retry_queue WHERE status = 'queued') AS retry_queued,
  (SELECT count(*) FROM public.retry_queue WHERE status = 'leased') AS retry_leased,
  (SELECT count(*) FROM public.retry_queue WHERE status = 'dead')   AS retry_dead
FROM r;

-- 7-2. 失敗原因TOP VIEW（7日）
CREATE OR REPLACE VIEW public.v_failed_reasons_7d AS
WITH f AS (
  SELECT
    failed_stage,
    left(coalesce(error_message, ''), 200) AS err200,
    failed_at
  FROM public."Rawdata_FILE_AND_MAIL"
  WHERE processing_status = 'failed'
    AND failed_at >= now() - interval '7 days'
)
SELECT
  coalesce(failed_stage, '(no_stage)') AS failed_stage,
  CASE
    WHEN err200 = '' THEN '(no_error)'
    ELSE err200
  END AS error_sample,
  count(*) AS cnt,
  max(failed_at) AS last_seen
FROM f
GROUP BY 1, 2;

-- 確認用: カラム一覧表示
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'Rawdata_FILE_AND_MAIL'
-- AND column_name LIKE 'stage_%'
-- ORDER BY ordinal_position;
