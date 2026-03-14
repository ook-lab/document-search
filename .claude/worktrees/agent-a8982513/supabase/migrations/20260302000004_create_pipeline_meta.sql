-- ============================================================
-- pipeline_meta テーブル作成（02_meta 相当）
-- パイプラインのキュー管理 + A ステージ解析結果
-- 01_raw の各行に対して 1:1 で対応
-- ============================================================

CREATE TABLE IF NOT EXISTS public.pipeline_meta (

  -- Identity
  id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  raw_id          UUID        NOT NULL,
  raw_table       TEXT        NOT NULL,
  UNIQUE (raw_id, raw_table),

  -- Common
  person          TEXT,
  source          TEXT,

  -- パイプラインキュー
  processing_status    TEXT        NOT NULL DEFAULT 'pending',
  processing_progress  FLOAT       NOT NULL DEFAULT 0.0,

  -- A ステージ解析結果
  origin_app           TEXT,
  origin_confidence    TEXT,
  layout_profile       TEXT,
  pdf_creator          TEXT,
  pdf_producer         TEXT,

  -- アクセス制御
  owner_id             UUID,

  -- System
  created_at           TIMESTAMPTZ DEFAULT now(),
  updated_at           TIMESTAMPTZ DEFAULT now()

);

CREATE INDEX IF NOT EXISTS idx_pm_raw           ON public.pipeline_meta (raw_table, raw_id);
CREATE INDEX IF NOT EXISTS idx_pm_person        ON public.pipeline_meta (person);
CREATE INDEX IF NOT EXISTS idx_pm_status        ON public.pipeline_meta (processing_status);
CREATE INDEX IF NOT EXISTS idx_pm_owner         ON public.pipeline_meta (owner_id);

DO $$
BEGIN
  RAISE NOTICE 'pipeline_meta テーブル作成完了';
END $$;
