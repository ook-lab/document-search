-- ============================================================
-- 09_unified_documents テーブル作成
-- 全ソース（Gmail / Calendar / Classroom / File）の処理済みデータを集約
-- 検索・表示の起点となるテーブル
-- ============================================================

CREATE TABLE IF NOT EXISTS public."09_unified_documents" (

  -- Identity
  id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  raw_id          UUID        NOT NULL,
  raw_table       TEXT        NOT NULL,

  -- 全ソース共通
  person          TEXT,
  source          TEXT,
  category        TEXT,
  title           TEXT,
  file_url        TEXT,
  from_email      TEXT,
  from_name       TEXT,

  -- Gmail固有
  snippet         TEXT,

  -- Calendar固有
  start_at        TIMESTAMPTZ,
  end_at          TIMESTAMPTZ,
  location        TEXT,

  -- Classroom固有
  due_date        DATE,
  post_type       TEXT,

  -- 投稿・送信日時
  post_at         TIMESTAMPTZ,

  -- パイプライン出力（J の入力）
  ui_data         JSONB,

  -- ソース固有補足（表示用）
  meta            JSONB,

  -- System
  indexed_at      TIMESTAMPTZ DEFAULT now()

);

CREATE INDEX IF NOT EXISTS idx_09_unified_person      ON public."09_unified_documents" (person);
CREATE INDEX IF NOT EXISTS idx_09_unified_source      ON public."09_unified_documents" (source);
CREATE INDEX IF NOT EXISTS idx_09_unified_category    ON public."09_unified_documents" (category);
CREATE INDEX IF NOT EXISTS idx_09_unified_post_at     ON public."09_unified_documents" (post_at DESC);
CREATE INDEX IF NOT EXISTS idx_09_unified_start_at    ON public."09_unified_documents" (start_at);
CREATE INDEX IF NOT EXISTS idx_09_unified_due_date    ON public."09_unified_documents" (due_date);
CREATE INDEX IF NOT EXISTS idx_09_unified_raw         ON public."09_unified_documents" (raw_table, raw_id);

DO $$
BEGIN
  RAISE NOTICE '09_unified_documents テーブル作成完了';
END $$;
